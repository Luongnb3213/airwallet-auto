from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spawn_throttle import spawn_slot


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
DEFAULT_FRIDA_PYTHON = sys.executable
DEFAULT_APP_ID = "jp.coinplus.app"


def import_frida():
    try:
        import frida  # type: ignore
    except ModuleNotFoundError:
        return None
    return frida


def candidate_frida_pythons() -> list[str]:
    candidates = [
        os.environ.get("FRIDA_PYTHON", ""),
        DEFAULT_FRIDA_PYTHON,
        shutil.which("python3") or "",
        sys.executable,
    ]
    seen: set[str] = set()
    out: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        path = str(Path(candidate).expanduser())
        if path in seen or not Path(path).exists():
            continue
        seen.add(path)
        out.append(path)
    return out


def can_import_frida(python_bin: str) -> bool:
    try:
        return subprocess.run(
            [python_bin, "-c", "import frida"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode == 0
    except OSError:
        return False


def find_frida_python() -> str:
    for python_bin in candidate_frida_pythons():
        if can_import_frida(python_bin):
            return python_bin
    raise RuntimeError("Không tìm thấy Python có module frida. Set FRIDA_PYTHON nếu cần.")


def frida_device_summary(frida) -> str:
    try:
        devices = frida.enumerate_devices()
    except Exception as exc:
        return f"không enumerate được device: {exc}"
    if not devices:
        return "không có device nào"
    return ", ".join(f"{device.id}/{device.name}/{device.type}" for device in devices)


def resolve_frida_device(frida, device_id: str, timeout: int = 10):
    if device_id and device_id.lower() != "auto":
        return frida.get_device(device_id)

    last_error = None
    for attempt in range(1, 4):
        try:
            return frida.get_usb_device(timeout=timeout if attempt == 1 else 3)
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                print(f"[airwallet-dom] Chưa thấy iPhone USB qua Frida, thử lại {attempt}/3...", file=sys.stderr)
                time.sleep(1.5)

    devices = frida_device_summary(frida)
    raise RuntimeError(
        "Frida chưa thấy iPhone USB. Kiểm tra cáp/Trust This Computer/frida-server trên iPhone. "
        f"Devices hiện thấy: {devices}. Lỗi gốc: {last_error}"
    )


def json_print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def load_agent() -> str:
    from driver import load_agent_source
    return load_agent_source()


def load_profile(path: str) -> dict:
    if not path:
        return {}
    p = Path(path).expanduser()
    if not p.exists():
        raise RuntimeError(f"Profile JSON not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "profile" in data and isinstance(data["profile"], dict):
        return data["profile"]
    if not isinstance(data, dict):
        raise RuntimeError(f"Profile JSON must be an object: {p}")
    return data


def app_pid(device, bundle_id: str) -> int | None:
    if not bundle_id:
        return None
    try:
        apps = device.enumerate_applications()
    except Exception:
        return None
    for app in apps:
        if getattr(app, "identifier", "") != bundle_id:
            continue
        pid = int(getattr(app, "pid", 0) or 0)
        return pid if pid > 0 else None
    return None


def wait_app_stopped(device, bundle_id: str, timeout_sec: float, poll_sec: float = 0.25) -> bool:
    deadline = time.time() + max(0, timeout_sec)
    while time.time() < deadline:
        if not app_pid(device, bundle_id):
            return True
        time.sleep(max(0.05, poll_sec))
    return not app_pid(device, bundle_id)


def springboard_pid(device) -> int | None:
    try:
        procs = device.enumerate_processes()
    except Exception:
        return None
    for proc in procs:
        if getattr(proc, "name", "") == "SpringBoard":
            pid = int(getattr(proc, "pid", 0) or 0)
            return pid if pid > 0 else None
    return None


WAKE_JS = r"""
rpc.exports = {
  wake: function () {
    var out = [];
    try {
      var p = Module.getGlobalExportByName('BKSDisplayServicesSetScreenBlanked');
      if (p) { new NativeFunction(p, 'void', ['uint32'])(0); out.push('unblank'); }
      else out.push('no_bks');
    } catch (e) { out.push('bks_err:' + e); }
    try {
      var dp = Module.getGlobalExportByName('IOPMAssertionDeclareUserActivity');
      var cs = Module.getGlobalExportByName('CFStringCreateWithCString');
      if (dp && cs) {
        var name = new NativeFunction(cs, 'pointer', ['pointer', 'pointer', 'uint32'])(
          ptr(0), Memory.allocUtf8String('airwallet-wake'), 0x08000100);
        var idOut = Memory.alloc(4);
        new NativeFunction(dp, 'int', ['pointer', 'int', 'pointer'])(name, 0, idOut);
        out.push('user_activity');
      } else out.push('no_iopm');
    } catch (e) { out.push('iopm_err:' + e); }
    return out.join(',');
  },
  reachable: function (host) {
    try {
      var create = Module.getGlobalExportByName('SCNetworkReachabilityCreateWithName');
      var getFlags = Module.getGlobalExportByName('SCNetworkReachabilityGetFlags');
      if (!create || !getFlags) return 'no_sc';
      var ref = new NativeFunction(create, 'pointer', ['pointer', 'pointer'])(
        ptr(0), Memory.allocUtf8String(host));
      if (ref.isNull()) return 'null_ref';
      var flagsPtr = Memory.alloc(4);
      var ok = new NativeFunction(getFlags, 'int', ['pointer', 'pointer'])(ref, flagsPtr);
      if (!ok) return 'getflags_fail';
      var flags = flagsPtr.readU32();
      var up = ((flags & (1 << 1)) !== 0) && ((flags & (1 << 2)) === 0);
      return (up ? 'up:' : 'down:') + flags;
    } catch (e) { return 'err:' + e; }
  }
};
"""


MEMINFO_JS = r"""
rpc.exports = {
  meminfo: function () {
    try {
      var hostSelf = new NativeFunction(Module.getGlobalExportByName('mach_host_self'), 'uint32', [])();
      var pagePtr = Memory.alloc(4);
      var pageResult = new NativeFunction(Module.getGlobalExportByName('host_page_size'), 'int', ['uint32', 'pointer'])(hostSelf, pagePtr);
      if (pageResult !== 0 || pagePtr.readU32() === 0) return 'err:host_page_size=' + pageResult;
      var PAGE = pagePtr.readU32();
      var hs64 = new NativeFunction(Module.getGlobalExportByName('host_statistics64'), 'int',
        ['uint32', 'int', 'pointer', 'pointer']);
      var info = Memory.alloc(38 * 4);
      var cnt = Memory.alloc(4); cnt.writeU32(38);
      var kr = hs64(hostSelf, 4, info, cnt);
      if (kr !== 0) return 'err:kr=' + kr;
      if (cnt.readU32() < 33) return 'err:short_vm_statistics64';
      var free = info.add(0).readU32();
      // XNU vm_statistics64: compressor_page_count is a uint32 at offset 128.
      var comp = info.add(128).readU32();
      var sysctl = new NativeFunction(Module.getGlobalExportByName('sysctlbyname'), 'int', ['pointer', 'pointer', 'pointer', 'pointer', 'ulong']);
      var size = Memory.alloc(8); size.writeU64(8);
      var total = Memory.alloc(8);
      if (sysctl(Memory.allocUtf8String('hw.memsize'), total, size, NULL, 0) !== 0) return 'err:hw.memsize';
      var machine = Memory.alloc(64); size.writeU64(64);
      if (sysctl(Memory.allocUtf8String('hw.machine'), machine, size, NULL, 0) !== 0) return 'err:hw.machine';
      return 'ok:free=' + free + ',comp=' + comp + ',ps=' + PAGE + ',total=' + total.readU64() + ',model=' + machine.readUtf8String();
    } catch (e) { return 'err:' + e; }
  }
};
"""


# Match only this app's notification/tracking prompts and one enabled denial action.
PERMISSION_POLICY_JS = r"""
function permissionDenialIndex(title, message, appName, actions) {
  function norm(text) {
    return String(text || '').normalize('NFC').replace(/’/g, "'").trim().toLowerCase();
  }
  if (!appName || norm(title).indexOf(norm(appName)) < 0) return -1;
  var text = norm(title + ' ' + message);
  var notification = /通知|notification|thông báo/.test(text);
  var tracking = /トラッキング|tracking|track you|theo dõi/.test(text);
  if (!notification && !tracking) return -1;
  var denied = ["許可しない", "don't allow", "không cho phép", "từ chối"];
  if (tracking) denied = denied.concat([
    "ask app not to track", "yêu cầu ứng dụng không theo dõi",
    "yêu cầu không theo dõi", "không cho phép theo dõi",
    "appにトラッキングしないように要求"
  ]);
  var matches = [];
  actions.forEach(function (action, index) {
    if (action.enabled && denied.indexOf(norm(action.title)) >= 0) matches.push(index);
  });
  return matches.length === 1 ? matches[0] : -1;
}
"""

# iOS permission prompts belong to SpringBoard, outside the app's view tree.
SB_DECLINE_JS = PERMISSION_POLICY_JS + r"""
var p = Module.getGlobalExportByName;
var msgPtr = p('objc_msgSend');
var objc_getClass = new NativeFunction(p('objc_getClass'), 'pointer', ['pointer']);
var sel_registerName = new NativeFunction(p('sel_registerName'), 'pointer', ['pointer']);
var object_getClassName = new NativeFunction(p('object_getClassName'), 'pointer', ['pointer']);
var object_getClass = new NativeFunction(p('object_getClass'), 'pointer', ['pointer']);
var object_getIvar = new NativeFunction(p('object_getIvar'), 'pointer', ['pointer', 'pointer']);
var class_getInstanceVariable = new NativeFunction(p('class_getInstanceVariable'), 'pointer', ['pointer', 'pointer']);
var dispatch_async_f = new NativeFunction(p('dispatch_async_f'), 'void', ['pointer', 'pointer', 'pointer']);
var mainQ = p('_dispatch_main_q');
function cls(n) { return objc_getClass(Memory.allocUtf8String(n)); }
function sel(n) { return sel_registerName(Memory.allocUtf8String(n)); }
function M(ret, args) { return new NativeFunction(msgPtr, ret, args); }
var sObj = M('pointer', ['pointer', 'pointer']);
var sIdx = M('pointer', ['pointer', 'pointer', 'uint64']);
var sCnt = M('uint64', ['pointer', 'pointer']);
var sBool = M('bool', ['pointer', 'pointer']);
var sChar = M('pointer', ['pointer', 'pointer']);
function cn(o) { if (o.isNull()) return '?'; var c = object_getClassName(o); return c.isNull() ? '?' : c.readUtf8String(); }
function toStr(ns) { if (ns.isNull()) return null; var c = sChar(ns, sel('UTF8String')); return c.isNull() ? null : c.readUtf8String(); }
var _pending = [];
function onMain(fn) {
  var cb = new NativeCallback(function () {
    try { fn(); } catch (e) {}
    var i = _pending.indexOf(cb); if (i >= 0) _pending.splice(i, 1);
  }, 'void', ['pointer']);
  _pending.push(cb);
  dispatch_async_f(mainQ, NULL, cb);
}
function alertController() {
  var app = sObj(cls('UIApplication'), sel('sharedApplication'));
  var wins = sObj(app, sel('windows')); var n = sCnt(wins, sel('count'));
  for (var i = 0; i < n; i++) {
    var w = sIdx(wins, sel('objectAtIndex:'), i);
    if (cn(w) !== 'SBAlertItemWindow') continue;
    var vc = sObj(w, sel('rootViewController')); var g = 0;
    while (!vc.isNull() && g++ < 12) {
      if (cn(vc).indexOf('AlertController') >= 0) return vc;
      vc = sObj(vc, sel('presentedViewController'));
    }
  }
  return NULL;
}
rpc.exports = {
  decline: function (appName) {
    var vc = alertController(); if (vc.isNull()) return 'none';
    var title = toStr(sObj(vc, sel('title'))) || '';
    var message = toStr(sObj(vc, sel('message'))) || '';
    var arr = sObj(vc, sel('actions'));
    if (sCnt(arr, sel('count')) < 1) return 'noactions';
    var actions = [];
    for (var j = 0; j < sCnt(arr, sel('count')); j++) {
      var candidate = sIdx(arr, sel('objectAtIndex:'), j);
      actions.push({title: toStr(sObj(candidate, sel('title'))), enabled: sBool(candidate, sel('isEnabled'))});
    }
    var index = permissionDenialIndex(title, message, appName, actions);
    if (index < 0) return 'skip:no-unique-deny-action';
    var a = sIdx(arr, sel('objectAtIndex:'), index);
    var iv = class_getInstanceVariable(object_getClass(a), Memory.allocUtf8String('_handler'));
    if (iv.isNull()) return 'nohandlerivar';
    var blk = object_getIvar(a, iv);
    if (blk.isNull()) return 'nullblock';
    onMain(function () {
      new NativeFunction(blk.add(16).readPointer(), 'void', ['pointer', 'pointer'])(blk, a);
    });
    return 'tapped:' + title + ' -> ' + toStr(sObj(a, sel('title')));
  }
};
"""


class SpringBoardAlertGuard:
    """Luồng nền: trong lúc DOM chạy, cứ ~1s quét SpringBoard và từ chối modal xin quyền của app."""

    def __init__(self, device, app_name: str = "エアウォレット", interval: float = 1.0):
        import threading

        self.app_name = app_name
        self.interval = interval
        self.session = None
        self.script = None
        self.tapped: list[str] = []
        self._stop = threading.Event()
        pid = springboard_pid(device)
        if not pid:
            raise RuntimeError("không thấy SpringBoard")
        self.session = device.attach(pid)
        self.script = self.session.create_script(SB_DECLINE_JS)
        self.script.load()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def sweep(self) -> str:
        res = str(self.script.exports_sync.decline(self.app_name))
        if res.startswith("tapped:"):
            self.tapped.append(res)
            print(f"[airwallet-dom] Modal iOS: {res}", file=sys.stderr)
        return res

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.sweep()
            except Exception:
                pass

    def close(self) -> None:
        self._stop.set()
        try:
            self._thread.join(timeout=3)
        except Exception:
            pass
        if self.session:
            try:
                self.session.detach()
            except Exception:
                pass


def start_alert_guard(device, args: argparse.Namespace):
    if getattr(args, "no_modal_guard", False):
        return None
    try:
        guard = SpringBoardAlertGuard(device)
        guard.sweep()
        return guard
    except Exception as exc:
        print(f"[airwallet-dom] Không bật được guard modal iOS: {exc}", file=sys.stderr)
        return None


MEASURE_HOST = "www.apple.com"


def wake_screen(device, bundle_id: str, t0: float | None = None, device_id: str = "",
                wait_network: bool = False, net_timeout: float = 60.0) -> dict:
    out = {"wake": "skip_no_springboard", "net_back_sec": None, "net_last": ""}
    pid = springboard_pid(device)
    if not pid:
        return out
    session = None
    try:
        session = device.attach(pid)
        script = session.create_script(WAKE_JS)
        script.load()
        out["wake"] = script.exports_sync.wake()
        if wait_network:
            base = t0 if t0 is not None else time.time()
            recovered = None
            last = ""
            deadline = time.time() + float(net_timeout)
            while time.time() < deadline:
                try:
                    last = script.exports_sync.reachable(MEASURE_HOST)
                except Exception as exc:
                    last = f"rpc_err:{exc}"
                if last.startswith("up:"):
                    recovered = time.time() - base
                    break
                if last.startswith(("no_sc", "null_ref", "getflags_fail")):
                    break
                time.sleep(1.0)
            out["net_back_sec"] = recovered
            out["net_last"] = last
            if recovered is not None:
                print(f"[airwallet-dom] Mạng đã về sau {recovered:.1f}s (reachability sau PowerSelector).", file=sys.stderr)
            else:
                print(f"[airwallet-dom] Chưa xác nhận mạng về trong {net_timeout:.0f}s (last={last}).", file=sys.stderr)
        return out
    except Exception as exc:
        out["wake"] = f"lỗi: {exc}"
        return out
    finally:
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass


def read_meminfo(device, bundle_id: str = "") -> dict:
    out = {"ok": False, "action": "meminfo", "free_mb": None, "compressor_mb": None, "raw": ""}
    pid = springboard_pid(device)
    if not pid:
        out["raw"] = "no_springboard"
        return out
    session = None
    try:
        session = device.attach(pid)
        script = session.create_script(MEMINFO_JS)
        script.load()
        raw = script.exports_sync.meminfo()
        out["raw"] = raw
        if isinstance(raw, str) and raw.startswith("ok:"):
            values, _, model = raw[3:].partition(',model=')
            fields = dict(p.split("=", 1) for p in values.split(",") if "=" in p)
            ps = int(fields.get("ps", "16384"))
            out["free_mb"] = round(int(fields.get("free", "0")) * ps / 1048576, 1)
            out["compressor_mb"] = round(int(fields.get("comp", "0")) * ps / 1048576, 1)
            out["page_size"] = ps
            out["total_mb"] = round(int(fields["total"]) / 1048576, 1)
            out["model"] = model
            out["ok"] = True
        return out
    except Exception as exc:
        out["raw"] = f"lỗi: {exc}"
        return out
    finally:
        if session:
            try:
                session.detach()
            except Exception:
                pass


def respring_springboard(device, wait_sec: float, bundle_id: str = "", net_timeout: float = 60.0) -> dict:
    from powerselector import request_respring
    pid = springboard_pid(device)
    if not pid:
        return {"ok": False, "action": "respring", "reason": "không thấy process SpringBoard"}
    t0 = time.time()
    try:
        request_respring(device)
    except Exception as exc:
        return {"ok": False, "action": "respring", "reason": str(exc)}
    print(f"[airwallet-dom] Đã chọn Respring trong PowerSelector; chờ SpringBoard đổi PID (cũ={pid}).", file=sys.stderr)
    time.sleep(max(0.0, float(wait_sec)))

    new_sb = None
    wait_deadline = time.time() + 45.0
    while time.time() < wait_deadline:
        new_sb = springboard_pid(device)
        if new_sb and new_sb != pid:
            break
        time.sleep(1.0)

    device_id = getattr(device, "id", "") or ""
    recovered = bool(new_sb and new_sb != pid)
    if recovered:
        wake = wake_screen(device, bundle_id, t0=t0, device_id=device_id, wait_network=True, net_timeout=net_timeout)
    else:
        wake = {"wake": "skip_no_springboard", "net_back_sec": None, "net_last": ""}
    print(f"[airwallet-dom] Wake + network gate sau respring: {wake}", file=sys.stderr)
    return {
        "ok": recovered,
        "action": "respring",
        "method": "powerselector",
        "reason": "" if recovered else "Chưa xác nhận SpringBoard đổi PID sau PowerSelector; không kill trực tiếp hay thử lại.",
        "springboard_pid": pid,
        "springboard_new_pid": new_sb,
        "wake": wake.get("wake"),
        "net_back_sec": wake.get("net_back_sec"),
        "net_ok": wake.get("net_back_sec") is not None,
    }


def kill_running_app(device, bundle_id: str, timeout_sec: float, ensure: bool = False) -> dict:
    out = {"stopped": True, "killed_pid": None}
    pid = app_pid(device, bundle_id)
    if not pid and ensure:
        appear_deadline = time.time() + min(max(0.0, timeout_sec), 4.0)
        while time.time() < appear_deadline:
            time.sleep(0.25)
            pid = app_pid(device, bundle_id)
            if pid:
                break
    if not pid:
        return out
    try:
        device.kill(pid)
        print(f"[airwallet-dom] Đã kill app {bundle_id} pid={pid}.", file=sys.stderr)
    except Exception as exc:
        print(f"[airwallet-dom] Kill app {bundle_id} pid={pid} lỗi tạm: {exc}", file=sys.stderr)
    out["killed_pid"] = pid
    out["stopped"] = wait_app_stopped(device, bundle_id, timeout_sec)
    if not out["stopped"]:
        print(f"[airwallet-dom] App {bundle_id} vẫn còn pid sau khi chờ tắt.", file=sys.stderr)
    return out


def spawn_attach_resume(device, args: argparse.Namespace, errors: list[str]):
    last_exc: Exception | None = None
    attempts = max(1, int(args.spawn_attempts))
    backoff = max(0.0, float(args.spawn_backoff_sec))
    for attempt in range(1, attempts + 1):
        pid = None
        try:
            with spawn_slot(slots=args.spawn_slots, label=f"dom {args.bundle_id}"):
                pid = device.spawn([args.bundle_id])
                device.resume(pid)
                # Suspended cold launches can fail Frida's dyld probe on these phones.
                time.sleep(1.0)
                session = device.attach(pid)
            print(f"[airwallet-dom] Đã mở app {args.bundle_id} pid={pid}.", file=sys.stderr)
            time.sleep(max(0, float(args.launch_wait)))
            return session, True
        except Exception as exc:
            last_exc = exc
            errors.append(f"spawn app {args.bundle_id} attempt {attempt}/{attempts}: {exc}")
            if pid:
                try:
                    device.kill(pid)
                except Exception:
                    pass
            if attempt < attempts:
                print(
                    f"[airwallet-dom] Spawn app lỗi tạm ({attempt}/{attempts}): {exc}; chờ {backoff:.1f}s rồi thử lại.",
                    file=sys.stderr,
                )
                time.sleep(backoff)
    if last_exc:
        raise last_exc
    raise RuntimeError("spawn failed")


def attach_or_launch(device, args: argparse.Namespace):
    errors: list[str] = []

    if args.fresh_launch and args.bundle_id:
        kill_running_app(device, args.bundle_id, args.kill_wait_sec)
        try:
            return spawn_attach_resume(device, args, errors)
        except Exception as exc:
            errors.append(f"fresh spawn app {args.bundle_id}: {exc}")
            raise RuntimeError("Không attach/mở được AirWallet app. " + " | ".join(errors))

    if args.process:
        try:
            session = device.attach(args.process)
            print(f"[airwallet-dom] Đã attach process {args.process}.", file=sys.stderr)
            return session, False
        except Exception as exc:
            errors.append(f"attach process {args.process}: {exc}")

    pid = app_pid(device, args.bundle_id)
    if pid:
        try:
            session = device.attach(pid)
            print(f"[airwallet-dom] Đã attach app {args.bundle_id} pid={pid}.", file=sys.stderr)
            return session, False
        except Exception as exc:
            errors.append(f"attach app pid={pid}: {exc}")

    if args.bundle_id:
        try:
            return spawn_attach_resume(device, args, errors)
        except Exception as exc:
            errors.append(f"spawn app {args.bundle_id}: {exc}")

    raise RuntimeError("Không attach/mở được AirWallet app. " + " | ".join(errors))


def run_airwallet(ex, profile, args):
    from flow import run_signup, build_otp_client
    directory = ROOT_DIR / 'dumps' / (time.strftime('%Y%m%d-%H%M%S') + f'-dom-{os.getpid()}')
    count = 0
    def progress(step, snapshot):
        nonlocal count
        count += 1
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f'{count:02d}-{step["action"]}.json').write_text(
            json.dumps({'step': step, 'snapshot': snapshot}, ensure_ascii=False, indent=2), encoding='utf-8')
        print('[airwallet] ' + step['action'] + ' -> ' + str(step['next_state']), file=sys.stderr, flush=True)
    return run_signup(ex, profile=profile, args=args, otp_client=build_otp_client(args.settings), on_step=progress)


def run_with_frida(args: argparse.Namespace) -> Any:
    profile = {}
    if args.action in ("run", "step"):
        from flow import ensure_configured, validate_profile, load_settings, build_otp_client
        ensure_configured()
        profile = validate_profile(load_profile(args.profile_json))
        args.settings = load_settings()
        from inputs import select_device_id
        args.device_id = select_device_id(args.device_id, profile=profile, settings=args.settings)
        if args.fresh_launch and build_otp_client(args.settings) is None:
            raise ValueError('Chưa cấu hình API key OTPBase; chưa fresh-launch app.')
        if not getattr(args, 'proxy_prepared', False):
            if build_otp_client(args.settings) is None and not profile.get('otp'):
                raise ValueError('Chưa cấu hình API key OTPBase; chưa đổi proxy hay mở app để đăng ký.')
            from src.core.airwallet_proxy import prepare_profile_proxy
            proxy_result = prepare_profile_proxy(profile, args, args.settings)
            profile.update({key: proxy_result[key] for key in ('proxy_id', 'proxy_used')})
            print('[proxy] ' + proxy_result['proxy_id'] + ' ready, exit IP ' + proxy_result['ip'], file=sys.stderr, flush=True)
    frida = import_frida()
    if frida is None:
        raise RuntimeError("Current Python cannot import frida.")

    device = resolve_frida_device(frida, args.device_id, timeout=10)
    if args.action == "kill":
        res = kill_running_app(device, args.bundle_id, args.kill_wait_sec, ensure=args.ensure_killed)
        return {"ok": bool(res["stopped"]), "action": "kill", "bundle_id": args.bundle_id,
                "stopped": res["stopped"], "killed_pid": res["killed_pid"]}
    if args.action == "respring":
        return respring_springboard(device, args.respring_wait, args.bundle_id, net_timeout=args.respring_net_timeout)
    if args.action == "meminfo":
        return read_meminfo(device, args.bundle_id)

    session, _ = attach_or_launch(device, args)
    guard = start_alert_guard(device, args) if args.action != "screen" else None

    def _on_detached(reason, crash=None):
        print(f"[airwallet-dom] session detached: reason={reason}", file=sys.stderr)
        if crash is not None:
            print("[airwallet-dom] crash report (%s): %s | %s" % (getattr(crash, "process_name", "?"), str(getattr(crash, "summary", ""))[:300], str(getattr(crash, "report", "")).replace(chr(10), " / ")[:2500]), file=sys.stderr)

    session.on("detached", _on_detached)
    try:
        script = session.create_script(load_agent())
        script.on("message", lambda message, data: print(message.get("stack") or message, file=sys.stderr) if message.get("type") == "error" else (print(message.get("payload"), file=sys.stderr, flush=True) if message.get("type") == "log" and str(message.get("payload", "")).startswith("[agent]") else None))
        script.load()
        time.sleep(args.load_wait)
        ex = script.exports_sync
        if args.action == "screen":
            return ex.snapshot()
        if args.action == "step":
            args.single_step = True
            old_max = args.max_steps
            args.max_steps = 1
            try:
                return run_airwallet(ex, profile, args)
            finally:
                args.max_steps = old_max
        return run_airwallet(ex, profile, args)
    finally:
        if guard:
            guard.close()
        session.detach()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the verified AirWallet iOS registration flow through Frida.")
    parser.add_argument("--process", default="")
    parser.add_argument("--bundle-id", default=os.environ.get("AIRWALLET_APP_ID", DEFAULT_APP_ID))
    parser.add_argument("--device-id", default=os.environ.get("FRIDA_DEVICE_ID", "auto"))
    parser.add_argument("--profile-json", default=str(ROOT_DIR / "agents" / "runtime" / "current_profile.json"))
    parser.add_argument('--xlsx', type=Path)
    parser.add_argument('--sheet')
    parser.add_argument('--row', type=int)
    parser.add_argument("--action", choices=["run", "step", "screen", "kill", "respring", "meminfo"], default="screen")
    parser.add_argument("--max-steps", type=int, default=40)
    parser.add_argument("--delay-ms", type=int, default=500)
    parser.add_argument("--poll-ms", type=int, default=500)
    parser.add_argument("--wait-timeout-ms", type=int, default=15000)
    parser.add_argument("--load-wait", type=float, default=0.5)
    parser.add_argument("--launch-wait", type=float, default=5.0)
    parser.add_argument("--fresh-launch", action="store_true", help="Kill the app first, then spawn it fresh for a new Crane container.")
    parser.add_argument("--spawn-attempts", type=int, default=4)
    parser.add_argument("--spawn-backoff-sec", type=float, default=1.5)
    parser.add_argument("--spawn-slots", type=int, default=None)
    parser.add_argument("--kill-wait-sec", type=float, default=5.0)
    parser.add_argument("--ensure-killed", action="store_true")
    parser.add_argument("--respring-wait", type=float, default=8.0)
    parser.add_argument("--respring-net-timeout", type=float, default=60.0)
    parser.add_argument("--no-submit", action="store_true", help="Stop before PIN confirmation/account creation.")
    parser.add_argument("--submit-final", action="store_true", help="Compatibility option; creation is enabled unless --no-submit.")
    parser.add_argument("--no-modal-guard", action="store_true", help="Không tự từ chối modal xin quyền thông báo của iOS.")
    parser.add_argument("--_child", action="store_true", help=argparse.SUPPRESS)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if import_frida() is None and not args._child:
            python_bin = find_frida_python()
            cmd = [python_bin, str(Path(__file__).resolve()), *sys.argv[1:], "--_child"]
            completed = subprocess.run(cmd, text=True, capture_output=True, check=False)
            if completed.stdout:
                print(completed.stdout, end="")
            if completed.stderr:
                print(completed.stderr, end="", file=sys.stderr)
            return completed.returncode

        result = run_with_frida(args)
        json_print(result)
        return 2 if isinstance(result, dict) and result.get('ok') is False else 0
    except Exception as exc:
        print(f"[airwallet-dom] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
