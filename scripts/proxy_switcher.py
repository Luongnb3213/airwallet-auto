#!/usr/bin/env python3
"""Đổi proxy trên iPhone qua tweak ProxySwitcher-NG (io.ymuu.proxyswitcherng) bằng Frida.

Tweak không có CLI nên script này: (1) lưu user/pass vào keychain qua PSNCredentialClient (chạy
trong cranehelperd, root), (2) ghi prefs + post Darwin notification từ SpringBoard (user mobile),
(3) kill daemon proxyswitcherngd (KeepAlive -> launchd dựng lại sau ~1s và đọc credential mới).

Vì sao kill daemon thay vì tắt/bật tweak: daemon chỉ nạp lại credential khi khởi động. Tắt/bật
làm máy đi thẳng ra IP thật trong lúc tắt; kill daemon thì system proxy vẫn trỏ vào relay
127.0.0.1 nên kết nối chỉ lỗi tạm chứ không rò ra IP thật (fail-closed).

Luồng đăng ký dùng tab Proxies của Excel: mỗi số điện thoại giữ một proxy riêng.
--action apply gán/áp dụng theo row, --action test chỉ thử một proxy_id. status là mặc định.
rotate/off là công cụ thủ công tương thích cấu hình cũ, không được batch gọi để đổi proxy
giữa các lần thử của cùng tài khoản.

    python scripts/proxy_switcher.py --device-id 45c12861 --action rotate --proxy-index 0
    python scripts/proxy_switcher.py --device-id 45c12861 --action status
    python scripts/proxy_switcher.py --device-id 45c12861 --action off

In 1 dòng JSON kết quả cuối cùng; exit 0 nếu ok.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
from src.core.airwallet_proxy import parse_proxy
PREF_DOMAIN = "io.ymuu.proxyswitcherng"
BUNDLE_REL = "/Library/PreferenceBundles/ProxySwitcherNG.bundle/ProxySwitcherNG"
DAEMON = "proxyswitcherngd"
IP_URLS = ("https://api.ipify.org", "https://ifconfig.me/ip")

COMMON_JS = r"""
const g = n => Module.getGlobalExportByName(n);
const getClass = new NativeFunction(g('objc_getClass'), 'pointer', ['pointer']);
const sel = new NativeFunction(g('sel_registerName'), 'pointer', ['pointer']);
const mp = g('objc_msgSend');
const S = s => sel(Memory.allocUtf8String(s));
const C = s => getClass(Memory.allocUtf8String(s));
const p0 = new NativeFunction(mp, 'pointer', ['pointer', 'pointer']);
const p1 = new NativeFunction(mp, 'pointer', ['pointer', 'pointer', 'pointer']);
const nsstr = s => p1(C('NSString'), S('stringWithUTF8String:'), Memory.allocUtf8String(s));
const str = o => o.isNull() ? null : p0(o, S('UTF8String')).readUtf8String();
"""

# SpringBoard (mobile): jbroot, prefs, Darwin notify.
SB_JS = COMMON_JS + r"""
const cfs = new NativeFunction(g('CFStringCreateWithCString'), 'pointer', ['pointer', 'pointer', 'uint']);
const setV = new NativeFunction(g('CFPreferencesSetValue'), 'void', ['pointer', 'pointer', 'pointer', 'pointer', 'pointer']);
const syncF = new NativeFunction(g('CFPreferencesSynchronize'), 'bool', ['pointer', 'pointer', 'pointer']);
const notifyPost = new NativeFunction(g('notify_post'), 'uint', ['pointer']);
const CF = s => cfs(ptr(0), Memory.allocUtf8String(s), 0x08000100);
const kUser = g('kCFPreferencesCurrentUser').readPointer();
const kHost = g('kCFPreferencesAnyHost').readPointer();
const kTrue = g('kCFBooleanTrue').readPointer();
const kFalse = g('kCFBooleanFalse').readPointer();
rpc.exports = {
  jbroot() {
    const m = Process.enumerateModules().find(x => x.name === 'libellekit.dylib');
    return m ? m.path.replace('/usr/lib/libellekit.dylib', '') : null;
  },
  apply(domain, host, port, enabled) {
    const dom = CF(domain);
    const set = (k, v) => setV(CF(k), v, dom, kUser, kHost);
    set('server', CF(host)); set('port', CF(String(port)));
    set('useSocks', kFalse); set('manualAuth', kTrue);
    set('enabled', enabled ? kTrue : kFalse);
    const ok = syncF(dom, kUser, kHost);
    notifyPost(Memory.allocUtf8String(domain + '/settingschanged'));
    return ok;
  }
};
"""

# cranehelperd (root): keychain credentials via the tweak's own client class, kill, exit IP probe.
HELPER_JS = COMMON_JS + r"""
const dlopen = new NativeFunction(g('dlopen'), 'pointer', ['pointer', 'int']);
const setH = new NativeFunction(mp, 'void', ['pointer', 'pointer', 'pointer', 'int', 'bool', 'pointer', 'pointer']);
const getH = new NativeFunction(mp, 'bool', ['pointer', 'pointer', 'pointer', 'int', 'bool', 'pointer', 'pointer']);
const reqNew = new NativeFunction(mp, 'pointer', ['pointer', 'pointer', 'pointer', 'ulong', 'double']);
const syncReq = new NativeFunction(mp, 'pointer', ['pointer', 'pointer', 'pointer', 'pointer', 'pointer']);
const initData = new NativeFunction(mp, 'pointer', ['pointer', 'pointer', 'pointer', 'ulong']);
const killF = new NativeFunction(g('kill'), 'int', ['int', 'int']);
const CFNetCopy = new NativeFunction(g('CFNetworkCopySystemProxySettings'), 'pointer', []);
rpc.exports = {
  setCreds(bundle, host, port, user, pw) {
    if (dlopen(Memory.allocUtf8String(bundle), 2).isNull()) return { ok: false, err: 'dlopen bundle' };
    const cls = C('PSNCredentialClient');
    if (cls.isNull()) return { ok: false, err: 'no PSNCredentialClient' };
    setH(cls, S('setHost:port:socks:username:password:'), nsstr(host), port, 0, nsstr(user), nsstr(pw));
    const ou = Memory.alloc(8), op = Memory.alloc(8); ou.writePointer(ptr(0)); op.writePointer(ptr(0));
    const ok = getH(cls, S('getHost:port:socks:username:password:'), nsstr(host), port, 0, ou, op);
    return { ok: !!ok && str(ou.readPointer()) === user && str(op.readPointer()) === pw };
  },
  killPid(pid) { return killF(pid, 15); },
  proxyOn() {
    return str(p0(CFNetCopy(), S('description'))).indexOf('HTTPEnable = 1') >= 0;
  },
  exitIp(url) {
    const u = p1(C('NSURL'), S('URLWithString:'), nsstr(url));
    const req = reqNew(C('NSURLRequest'), S('requestWithURL:cachePolicy:timeoutInterval:'), u, 1, 15.0);
    const data = syncReq(C('NSURLConnection'), S('sendSynchronousRequest:returningResponse:error:'), req, ptr(0), ptr(0));
    if (data.isNull()) return null;
    return str(initData(p0(C('NSString'), S('alloc')), S('initWithData:encoding:'), data, 4));
  }
};
"""


def load_proxies() -> list[dict]:
    raw = os.environ.get("AIRWALLET_PROXIES", "").strip()
    items: list[str]
    if raw:
        items = [x for x in re.split(r"[;\r\n]+", raw) if x.strip()]
    else:
        try:
            value = json.loads((ROOT_DIR / "config.json").read_text(encoding="utf-8")).get("proxies") or []
        except Exception:
            value = []
        items = [str(x) for x in value if str(x).strip()]
    if not items:
        raise RuntimeError('Chưa cấu hình proxy: config.json "proxies": ["host:port:user:pass", ...] hoặc env AIRWALLET_PROXIES.')
    return [parse_proxy(x) for x in items]


class Phone:
    def __init__(self, device_id: str):
        import frida

        wanted = str(device_id or 'auto').strip().lower()
        matches = [d for d in frida.enumerate_devices() if d.type == 'usb' and (wanted == 'auto' or d.id.lower().startswith(wanted))]
        if len(matches) != 1:
            raise RuntimeError('Cần chọn đúng một iPhone USB để đổi proxy; kiểm tra device ID.')
        self.dev = matches[0]
        self.jbroot = ""

    def pid(self, process: str) -> int | None:
        return next((p.pid for p in self.dev.enumerate_processes() if p.name == process), None)

    def _call(self, process: str, js: str, fn):
        pid = self.pid(process)
        if pid is None:
            raise RuntimeError(f"Process {process} không chạy trên máy")
        session = self.dev.attach(pid)
        try:
            script = session.create_script(js)
            script.load()
            return fn(script.exports_sync)
        finally:
            try:
                session.detach()
            except Exception:
                pass

    def sb(self, fn):
        return self._call("SpringBoard", SB_JS, fn)

    def helper(self, fn):
        return self._call("cranehelperd", HELPER_JS, fn)

    def ensure_jbroot(self) -> str:
        self.jbroot = self.jbroot or self.sb(lambda ex: ex.jbroot()) or ""
        if not self.jbroot:
            raise RuntimeError("Không xác định được jbroot (ElleKit?)")
        return self.jbroot

    def exit_ip(self) -> str | None:
        for url in IP_URLS:
            try:
                ip = self.helper(lambda ex: ex.exitIp(url))
            except Exception:
                ip = None
            if ip:
                try:
                    address = ipaddress.ip_address(ip.strip())
                    if not address.is_unspecified and not address.is_loopback:
                        return str(address)
                except ValueError:
                    pass
        return None

    def set_proxy(self, proxy: dict) -> None:
        bundle = self.ensure_jbroot() + BUNDLE_REL
        res = self.helper(lambda ex: ex.setCreds(bundle, proxy["host"], proxy["port"], proxy["user"], proxy["password"]))
        if not res.get("ok"):
            raise RuntimeError(f"Lưu credential vào tweak thất bại: {res}")

    def enable(self, proxy: dict, enabled: bool) -> None:
        if not self.sb(lambda ex: ex.apply(PREF_DOMAIN, proxy["host"], proxy["port"], enabled)):
            raise RuntimeError("Ghi prefs ProxySwitcher-NG thất bại")

    def restart_daemon(self, timeout: float = 15.0) -> None:
        old = self.pid(DAEMON)
        if old is None:
            raise RuntimeError(f"{DAEMON} không chạy (tweak chưa cài/chưa bật?)")
        self.helper(lambda ex: ex.killPid(old))
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.5)
            new = self.pid(DAEMON)
            if new and new != old:
                return
        raise RuntimeError(f"{DAEMON} không tự dựng lại sau {timeout:.0f}s")


def rotate(phone: Phone, proxies: list[dict], start_index: int, attempts: int = 3) -> dict:
    prev_ip = phone.exit_ip()
    last_err = "không có IP mới"
    tries = min(max(1, attempts), len(proxies)) if len(proxies) > 1 else 1
    for attempt in range(tries):
        index = (start_index + attempt) % len(proxies)
        proxy = proxies[index]
        phone.set_proxy(proxy)
        phone.enable(proxy, True)
        phone.restart_daemon()
        ip = None
        time.sleep(2.0)
        for _ in range(8):  # relay cần vài giây dựng lại; đọc sớm có thể lỗi hoặc còn IP cũ
            ip = phone.exit_ip()
            if ip and ip != prev_ip:
                break
            time.sleep(2.0)
        if ip and (ip != prev_ip or len(proxies) == 1):
            return {"ok": True, "ip": ip, "prev_ip": prev_ip, "proxy_index": index, "attempt": attempt + 1}
        last_err = f"IP thoát {'không đổi' if ip else 'không đọc được'} ({ip}) sau khi chuyển sang proxy #{index}"
    return {"ok": False, "error": last_err, "prev_ip": prev_ip}


def apply_proxy(phone: Phone, proxy: dict, timeout: float = 30.0) -> dict:
    """Apply only this account's proxy. The same exit IP on resume is valid."""
    phone.set_proxy(proxy)
    phone.enable(proxy, True)
    phone.restart_daemon()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if phone.helper(lambda ex: ex.proxyOn()):
            ip = phone.exit_ip()
            if ip:
                return {'ok': True, 'proxy_on': True, 'ip': ip}
        time.sleep(1)
    return {'ok': False, 'error': 'ProxySwitcher-NG chưa bật proxy hoặc chưa đọc được IP thoát; không đổi sang proxy khác.'}


def main() -> int:
    parser = argparse.ArgumentParser(description="Đổi proxy iPhone qua ProxySwitcher-NG.")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--action", choices=("rotate", "status", "off", "apply", "test"), default="status")
    parser.add_argument("--proxy-index", type=int, default=0, help="Vị trí proxy bắt đầu trong danh sách (xoay vòng).")
    parser.add_argument('--xlsx', type=Path)
    parser.add_argument('--sheet', default='Inputs')
    parser.add_argument('--row', type=int)
    parser.add_argument('--proxy-id', default='')
    args = parser.parse_args()
    try:
        if args.action == 'apply':
            from src.core.airwallet_proxy import prepare_account_proxy
            if not args.xlsx or not args.row:
                raise ValueError('apply cần --xlsx và --row.')
            result = {'ok': True, **prepare_account_proxy(args.xlsx, args.sheet, args.device_id, row=args.row)}
            print(json.dumps(result, ensure_ascii=False))
            return 0
        phone = Phone(args.device_id)
        if args.action == "status":
            print(json.dumps({"ok": True, "proxy_on": phone.helper(lambda ex: ex.proxyOn()), "ip": phone.exit_ip()}))
            return 0
        if args.action == 'test':
            import openpyxl
            from src.core.airwallet_proxy import record_check
            if not args.xlsx or not args.proxy_id:
                raise ValueError('test cần --xlsx và --proxy-id; chỉ đổi proxy, không tạo tài khoản.')
            book = openpyxl.load_workbook(args.xlsx, read_only=True, data_only=True)
            try:
                sheet = book['Proxies']
                rows = sheet.iter_rows(values_only=True)
                headers = [str(value or '').strip() for value in next(rows)]
                matches = [dict(zip(headers, row)) for row in rows if str(row[headers.index('proxy_id')] or '') == args.proxy_id]
                if len(matches) != 1:
                    raise ValueError('Không tìm được duy nhất proxy_id trong tab Proxies.')
                if str(matches[0].get('status') or 'ACTIVE').upper() not in ('ACTIVE', 'ASSIGNED'):
                    raise ValueError('Proxy đã bị tắt trong tab Proxies.')
                proxy = parse_proxy(matches[0].get('proxy'))
            finally:
                book.close()
            result = apply_proxy(phone, proxy)
            record_check(args.xlsx, args.proxy_id, result)
            print(json.dumps({**result, 'proxy_id': args.proxy_id}, ensure_ascii=False))
            return 0 if result.get('ok') else 1
        proxies = load_proxies()
        if args.action == "off":
            phone.enable(proxies[0], False)
            time.sleep(2.0)
            print(json.dumps({"ok": True, "proxy_on": False, "ip": phone.exit_ip()}))
            return 0
        result = rotate(phone, proxies, args.proxy_index)
    except Exception as exc:  # noqa: BLE001 - CLI: báo lỗi gọn cho batch
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
