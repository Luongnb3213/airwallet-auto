import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frida_stub import install_frida_stub

install_frida_stub()  # no-op when the real frida is installed
import driver
import main

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import airwallet_profile_from_excel as reader
import airwallet_dom_runner as runner
import airwallet_full_flow as full
import crane_container_manager as crane

PROFILE = {'phone_number': '07012345678', 'account_password': 'TestOnly123',
           'kana_sei': 'テスト', 'kana_mei': 'タロウ', 'birthday': '1999-02-03', 'pin': '0042',
           'frida_device_id': 'row-device'}


class DeviceAssignmentTests(unittest.TestCase):
    def test_new_container_default_name_uses_phone_with_leading_zero(self):
        self.assertEqual(crane.default_container_name(PROFILE, 2), '07012345678')
        self.assertEqual(crane.default_container_name({'phone_number': '+81 70 1234 5678'}, 2), '07012345678')

    def test_container_name_without_phone_keeps_email_fallback(self):
        self.assertEqual(crane.default_container_name({'email': 'test@example.com'}, 2), 'AirWallet_test_r2')

    def test_existing_container_name_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'unused.xlsx'
            path.touch()
            args = crane.build_parser().parse_args(['ensure-row', '--xlsx', str(path), '--row', '2'])
            args.write_excel = False
            profile = dict(PROFILE, crane_container_id='existing-container', crane_container_name='Existing name')
            with patch.object(reader, 'load_row', return_value=(profile, 2, 'Inputs')), \
                 patch.object(crane, 'invoke_crane_rpc', return_value={
                     'ok': True, 'crane_container_id': 'existing-container',
                     'crane_container_name': 'Existing name'}) as call:
                result = crane.ensure_row(args)
            self.assertEqual(result['crane_container_name'], 'Existing name')
            self.assertEqual(call.call_count, 1)
            self.assertEqual(call.call_args.kwargs['container_id'], 'existing-container')
            self.assertEqual(call.call_args.kwargs['name'], 'Existing name')

    def test_existing_name_is_not_erased_when_crane_omits_display_name(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'unused.xlsx'
            path.touch()
            args = crane.build_parser().parse_args(['ensure-row', '--xlsx', str(path), '--row', '2'])
            args.write_excel = False
            profile = dict(PROFILE, crane_container_id='existing-container', crane_container_name='Existing name')
            with patch.object(reader, 'load_row', return_value=(profile, 2, 'Inputs')), \
                 patch.object(crane, 'invoke_crane_rpc', return_value={
                     'ok': True, 'crane_container_id': 'existing-container', 'crane_container_name': ''}):
                result = crane.ensure_row(args)
            self.assertEqual(result['crane_container_name'], 'Existing name')

    def test_main_auto_uses_row_and_explicit_device_overrides_it(self):
        for requested, expected in [('auto', 'row-device'), ('cli-device', 'cli-device')]:
            with self.subTest(requested=requested), \
                 patch.object(sys, 'argv', ['main.py', 'signup', '--device-id', requested]), \
                 patch.object(main, 'load_config', return_value={'device_id': 'config-device'}), \
                 patch.object(main, 'load_signup_input', return_value=PROFILE), \
                 patch.object(main, 'build_otp_client', return_value=object()), \
                 patch.object(main, 'prepare_profile_proxy', return_value={'proxy_id': 'PX-test', 'proxy_used': 'test:8080', 'ip': '192.0.2.1'}), \
                 patch.object(driver, 'connect', side_effect=RuntimeError('test stopped before attach')) as connect, \
                 contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main.main(), 1)
                self.assertEqual(connect.call_args.args[0], expected)

    def test_dom_auto_uses_profile_assignment_before_resolving_device(self):
        args = runner.build_parser().parse_args(['--action', 'run', '--device-id', 'auto'])
        args.proxy_prepared = True
        with patch.object(runner, 'load_profile', return_value=PROFILE), \
             patch('flow.load_settings', return_value={}), \
             patch.object(runner, 'resolve_frida_device', side_effect=RuntimeError('stop before attach')) as resolve:
            with self.assertRaisesRegex(RuntimeError, 'stop before attach'):
                runner.run_with_frida(args)
        self.assertEqual(resolve.call_args.args[1], 'row-device')

    def test_full_flow_passes_same_row_device_to_crane_and_signup(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sys, 'argv', ['full', '--xlsx', 'unused.xlsx', '--row', '2', '--device-id', 'auto']), \
                 patch.object(full, 'ROOT_DIR', Path(directory)), \
                 patch.object(reader, 'load_row', return_value=(PROFILE, 2, 'Inputs')), \
                 patch('flow.load_settings', return_value={}), \
                 patch('flow.build_otp_client', return_value=object()), \
                 patch.object(full, 'prepare_account_proxy', return_value={'proxy_id': 'PX-test', 'proxy_used': 'test:8080', 'ip': '192.0.2.1'}), \
                 patch.object(full, 'run_cmd_output', return_value='{"ok":true}') as create, \
                 patch.object(runner, 'run_with_frida', return_value={'complete': True, 'status': 'complete'}) as signup, \
                 patch.object(full, 'write_row_status'), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(full.main(), 0)
            command = create.call_args.args[0]
            self.assertEqual(command[command.index('--device-id') + 1], 'row-device')
            self.assertEqual(signup.call_args.args[0].device_id, 'row-device')

    def test_direct_crane_ensure_auto_uses_excel_device(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'unused.xlsx'
            path.touch()
            args = crane.build_parser().parse_args(['ensure-row', '--xlsx', str(path), '--row', '2', '--device-id', 'auto'])
            args.write_excel = False
            with patch.object(reader, 'load_row', return_value=(PROFILE, 2, 'Inputs')), \
                 patch.object(crane, 'invoke_crane_rpc', return_value={
                     'ok': True, 'crane_container_id': 'test-container', 'crane_container_name': 'Test container'}) as call:
                result = crane.ensure_row(args)
            self.assertTrue(result['ok'])
            self.assertEqual(call.call_args.args[0].device_id, 'row-device')


if __name__ == '__main__':
    unittest.main()
