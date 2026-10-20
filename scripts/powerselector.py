"""Respring through the observed PowerSelector UI, without killing SpringBoard."""
import time

from driver import load_agent_source
from spawn_throttle import spawn_slot

BUNDLE = 'com.ichitaso.powerselectorapp'
MESSAGE = 'Confirm Respring'

OPEN_PROMPT_JS = r'''
rpc.exports.openRespringPrompt = () => onMain(() => {
    if (string(ObjC.classes.NSBundle.mainBundle().bundleIdentifier()) !== 'com.ichitaso.powerselectorapp') {
        throw new Error('Wrong PowerSelector bundle.');
    }
    const vc = requireScreen('PSController');
    if (string(vc.title()) !== 'PowerSelector') throw new Error('Unexpected PowerSelector screen.');
    const specs = vc.specifiers();
    const selectorName = new NativeFunction(Module.getGlobalExportByName('sel_getName'), 'pointer', ['pointer']);
    let matches = 0;
    for (let i = 0; i < specs.count(); i++) {
        const spec = specs.objectAtIndex_(i);
        if (string(spec.name()) !== 'Respring') continue;
        const action = spec.$ivars.action;
        if (action && !action.isNull() && selectorName(action).readUtf8String() === 'tapRespring') matches++;
    }
    if (matches !== 1) throw new Error('Expected one observed Respring action.');
    vc.tapRespring();
    return true;
});
'''


def validate_confirmation(snapshot):
    alert = snapshot.get('alert') or {}
    return (snapshot.get('bundle') == BUNDLE and snapshot.get('topVC') == 'UIAlertController'
            and alert.get('message') == MESSAGE
            and sorted(alert.get('buttons') or []) == ['Cancel', 'Respring'])


def request_respring(device, timeout=10.0):
    """Open the tweak, validate its Respring prompt, select only that action once."""
    import frida
    app = next((app for app in device.enumerate_applications() if app.identifier == BUNDLE), None)
    if app is None:
        raise RuntimeError('Chưa cài PowerSelector (com.ichitaso.powerselectorapp).')
    # Freshly open only PowerSelector so a stale dialog or background app is not reused.
    session = None
    suspended = False
    pid = None
    try:
        with spawn_slot(timeout=10.0, label='PowerSelector respring'):
            if app.pid:
                device.kill(app.pid)
            pid = device.spawn([BUNDLE])
            suspended = True
            session = device.attach(pid)
            script = session.create_script(load_agent_source() + OPEN_PROMPT_JS)
            script.load()
            device.resume(pid)
            suspended = False
        api = script.exports_sync
        deadline = time.monotonic() + timeout
        while True:
            try:
                snapshot = api.snapshot()
            except frida.RPCException as error:
                if (not any(marker in str(error) for marker in ('No visible app window', 'No root controller'))
                        or time.monotonic() >= deadline):
                    raise
                time.sleep(0.2)
                continue
            if snapshot.get('topVC') == 'PSController':
                break
            if snapshot.get('alert') or time.monotonic() >= deadline:
                raise RuntimeError('PowerSelector chưa ở màn chính; giữ nguyên để kiểm tra.')
            time.sleep(0.2)
        api.open_respring_prompt()
        while True:
            snapshot = api.snapshot()
            if validate_confirmation(snapshot):
                break
            if snapshot.get('alert') or time.monotonic() >= deadline:
                raise RuntimeError('Không khớp hộp thoại Confirm Respring; chưa xác nhận.')
            time.sleep(0.2)
        try:
            if not api.choose_alert('Respring', MESSAGE):
                raise RuntimeError('PowerSelector không xác nhận thao tác Respring.')
        except (frida.InvalidOperationError, frida.TransportError, frida.ProcessNotFoundError):
            # The UI process may disappear while its confirmation RPC returns.
            # The caller must still prove that SpringBoard got a different PID.
            pass
    finally:
        try:
            if suspended and pid:
                device.resume(pid)
        finally:
            if session is not None:
                try:
                    session.detach()
                except Exception:
                    pass
