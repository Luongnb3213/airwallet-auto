"""Frida connection and snapshot formatting, adapted from moppy-auto/driver.py."""
from pathlib import Path
import time

import frida
import frida_tools

BUNDLE = 'jp.coinplus.app'
HERE = Path(__file__).resolve().parent


def load_agent_source():
    bridge_path = Path(frida_tools.__file__).parent / 'bridges' / 'objc.js'
    source = bridge_path.read_text(encoding='utf-8') + '\nconst ObjC = bridge;\n'
    return source + (HERE / 'agent.js').read_text(encoding='utf-8')


def connect(device_id='auto', wait_seconds=3):
    if device_id and device_id != 'auto':
        device = frida.get_device(device_id, timeout=10)
    else:
        devices = [d for d in frida.enumerate_devices() if d.type == 'usb']
        if len(devices) > 1:
            raise RuntimeError('Có nhiều thiết bị USB. Chọn một máy bằng --device-id.')
        device = devices[0] if devices else frida.get_usb_device(timeout=10)
    app = next((a for a in device.enumerate_applications() if a.identifier == BUNDLE), None)
    if app is None:
        raise RuntimeError(f'Chưa tìm thấy AirWallet ({BUNDLE}) trên {device.name}.')
    # Two separate facts: whether the process is still suspended right now, and
    # whether we are the ones who launched it. The second decides who owns it on
    # the failure path, and it must survive the resume below.
    spawned_here = not app.pid
    suspended = spawned_here
    pid = app.pid or device.spawn([BUNDLE])
    session = None
    try:
        if suspended:
            device.resume(pid)
            suspended = False
            # UI automation needs a running app; let dyld initialize before attaching.
            time.sleep(1.0)
        session = device.attach(pid)
        script = session.create_script(load_agent_source())
        messages = []
        script.on('message', lambda message, data: messages.append(message))
        script.load()
        time.sleep(wait_seconds)
        return device, session, script, messages
    except BaseException:
        # connect() either hands back a working session or leaves no trace. A
        # process we spawned and could not attach to would otherwise stay on the
        # device, suspended or half-initialised, for nobody.
        # Each step is isolated so cleanup never masks the original failure.
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass
        if spawned_here:
            try:
                device.kill(pid)
            except Exception:
                pass
        raise


def pretty(snap):
    lines = [f"AirWallet: {snap.get('bundle')} {snap.get('version')}", f"topVC: {snap.get('topVC')}"]
    if snap.get('title'):
        lines.append('title: ' + snap['title'])
    if snap.get('alert'):
        lines.append('ALERT: ' + str(snap['alert']))
    for page in snap.get('pages', []):
        lines.append(f"page: {page['current'] + 1}/{page['count']}")
    for field in snap.get('fields', []):
        lines.append(f"field[{field['index']}]: {field.get('placeholder')!r} secure={field['secure']} filled={field['filled']}")
    for button in snap.get('buttons', []):
        lines.append(f"button[{button['index']}]: {button['title']!r} enabled={button['enabled']}")
    for label in snap.get('labels', []):
        lines.append('label: ' + label)
    return '\n'.join(lines)
