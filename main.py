"""Main entry point for AirWallet registration, independent of Moppy runners."""
import argparse
import json
import os
import re
from pathlib import Path
import sys
import time

import driver
from flow import ensure_configured, run_signup, validate_profile, build_otp_client
from inputs import load_signup_input, select_device_id
from diagnostics import diagnose, pretty_diagnosis
from src.core.airwallet_proxy import prepare_profile_proxy

ROOT = Path(__file__).resolve().parent


def load_config(path):
    if not path.exists():
        return {}
    result = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(result, dict):
        raise ValueError('config.json phải là một JSON object.')
    return result


def main():
    parser = argparse.ArgumentParser(description='AirWallet registration through Frida')
    parser.add_argument('action', nargs='?', choices=['signup', 'snapshot', 'dump', 'devices'], default='snapshot')
    parser.add_argument('--config', type=Path, default=ROOT / 'config.json')
    parser.add_argument('--device-id', default=os.environ.get('FRIDA_DEVICE_ID'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--label', default='', help='Name a guided dump without changing the screen')
    parser.add_argument('--profile-json', type=Path)
    parser.add_argument('--xlsx', type=Path)
    parser.add_argument('--sheet')
    parser.add_argument('--row', type=int)
    parser.add_argument('--otp', help='Manual OTP only when resuming an existing SMS screen')
    parser.add_argument('--resume-dump', type=Path)
    parser.add_argument('--step', dest='single_step', action='store_true')
    parser.add_argument('--no-submit', action='store_true', help='Stop before confirming the PIN/account creation')
    parser.add_argument('--no-modal-guard', action='store_true')
    parser.add_argument('--max-steps', type=int, default=40)
    parser.add_argument('--wait-timeout-ms', type=int, default=15000)
    parser.add_argument('--poll-ms', type=int, default=400)
    args = parser.parse_args()
    session = None
    guard = None
    try:
        if args.action == 'devices':
            print(json.dumps([{'id': d.id, 'name': d.name, 'type': d.type}
                              for d in driver.frida.enumerate_devices() if d.type == 'usb'], ensure_ascii=False, indent=2))
            return 0
        config = load_config(args.config)
        profile = None
        if args.action == 'signup':
            ensure_configured()
            try:
                profile = validate_profile(load_signup_input(args, config))
            except (ValueError, RuntimeError, OSError) as error:
                print('[airwallet] ' + str(error), file=sys.stderr, flush=True)
                return 2
        args.settings = config
        launch_wait = float(config.get('launch_wait_seconds', 3))
        if launch_wait < 0:
            raise ValueError('launch_wait_seconds >= 0.')
        device_id = select_device_id(args.device_id, profile=profile, settings=config)
        proxy_result = None
        otp_client = None
        if args.action == 'signup':
            otp_client = build_otp_client(config)
            if otp_client is None and not profile.get('otp'):
                raise ValueError('Chưa cấu hình API key OTPBase; chưa đổi proxy hay mở app để đăng ký.')
            args.device_id = device_id
            proxy_result = prepare_profile_proxy(profile, args, config)
            profile.update({key: proxy_result[key] for key in ('proxy_id', 'proxy_used')})
            print('[proxy] ' + proxy_result['proxy_id'] + ' ready, exit IP ' + proxy_result['ip'], flush=True)
        device, session, script, messages = driver.connect(device_id, launch_wait)
        api = script.exports_sync
        if args.action in ('snapshot', 'dump'):
            result = {'ok': True, 'status': args.action, 'snapshot': api.snapshot()}
        else:
            from scripts.airwallet_dom_runner import start_alert_guard
            guard = start_alert_guard(device, args)
            dump_dir = ROOT / 'dumps' / (time.strftime('%Y%m%d-%H%M%S') + f'-signup-{os.getpid()}')
            def on_step(step, snap):
                dump_dir.mkdir(parents=True, exist_ok=True)
                path = dump_dir / (f"{len(list(dump_dir.glob('*.json'))) + 1:02d}-" + step['action'] + '.json')
                path.write_text(json.dumps({'step': step, 'snapshot': snap}, ensure_ascii=False, indent=2), encoding='utf-8')
                print('[step] ' + step['action'] + ' -> ' + str(step['next_state']), flush=True)
            result = run_signup(api, profile=profile, args=args, otp_client=otp_client, on_step=on_step)
            result['proxy'] = proxy_result
        result['device'] = {'id': device.id, 'name': device.name}
        result['diagnostics'] = diagnose(result['snapshot'])
        print(driver.pretty(result['snapshot']), flush=True)
        print(pretty_diagnosis(result['diagnostics']), flush=True)
        print('\nstatus: ' + result['status'], flush=True)
        if result.get('message'):
            print(result['message'], flush=True)
        label = re.sub(r'[^\w.-]+', '-', args.label).strip('.-') or args.action
        destination = args.output or ROOT / 'dumps' / (time.strftime('%Y%m%d-%H%M%S') + '-' + label + '.json')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        if args.action == 'dump':
            destination.with_suffix('.txt').write_text(driver.pretty(result['snapshot']) + '\n' +
                                                       pretty_diagnosis(result['diagnostics']), encoding='utf-8')
        print('Saved: ' + str(destination), flush=True)
        return 0 if result['ok'] else 2
    except (Exception, KeyboardInterrupt) as error:
        print('[airwallet] ' + (str(error) or 'Đã dừng.'), file=sys.stderr, flush=True)
        return 1
    finally:
        if guard is not None:
            guard.close()
        if session is not None:
            session.detach()


if __name__ == '__main__':
    raise SystemExit(main())
