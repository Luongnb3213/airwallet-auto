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
from flow import ensure_configured

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import airwallet_dom_runner as runner
import airwallet_full_flow as full_flow
import airwallet_batch_flow as batch_flow
from airwallet_profile_from_excel import profile_from_record
import airwallet_profile_from_excel as profile_reader
import crane_container_manager as crane


class WrapperTests(unittest.TestCase):
    def test_app_defaults_target_airwallet(self):
        self.assertEqual(driver.BUNDLE, 'jp.coinplus.app')
        self.assertEqual(runner.build_parser().parse_args([]).bundle_id, driver.BUNDLE)
        self.assertEqual(crane.DEFAULT_APP_ID, driver.BUNDLE)

    def test_default_runner_only_reads_screen(self):
        self.assertEqual(runner.build_parser().parse_args([]).action, 'screen')

    def test_signup_entry_does_not_connect_with_missing_input(self):
        with patch.object(sys, 'argv', ['main.py', 'signup']), \
             patch.object(main, 'load_signup_input', return_value={}), \
             patch.object(driver, 'connect') as connect:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main.main(), 2)
            connect.assert_not_called()

    def test_dom_run_validates_profile_before_touching_device(self):
        args = runner.build_parser().parse_args(['--action', 'run'])
        with patch.object(runner, 'resolve_frida_device') as resolve, patch.object(runner, 'load_profile', return_value={}):
            with self.assertRaisesRegex(ValueError, 'Thiếu dữ liệu'):
                runner.run_with_frida(args)
            resolve.assert_not_called()

    def test_full_wrapper_stops_before_creating_container(self):
        with patch.object(sys, 'argv', ['full', '--xlsx', 'unused.xlsx', '--row', '2']):
            with patch.object(full_flow, 'run_cmd_output') as run, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(full_flow.main(), 1)
                run.assert_not_called()

    def test_batch_stops_before_proxy_or_device_changes(self):
        with patch.object(sys, 'argv', ['batch', '--xlsx', 'unused.xlsx']):
            with patch.object(batch_flow, 'resolve_device_ids') as resolve, patch('flow.build_otp_client', return_value=None), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(batch_flow.main(), 2)
                resolve.assert_not_called()

    def test_profile_preserves_password_and_nickname(self):
        profile = profile_from_record({'email': 'test@example.com', 'password': 'Some@Password-123!', 'nickname': 'some.name-12345'})
        self.assertEqual(profile['password'], 'Some@Password-123!')
        self.assertEqual(profile['nickname'], 'some.name-12345')

    def test_full_wrapper_marks_only_completed_run_successful(self):
        profile = {'phone_number': '07012345678', 'account_password': 'TestOnly123',
                   'kana_sei': 'テスト', 'kana_mei': 'タロウ', 'birthday': '1999-02-03', 'pin': '0042'}
        cases = [({'ok': True, 'complete': True, 'status': 'complete'}, 'SUCCESS', 0),
                 ({'ok': True, 'complete': False, 'status': 'already_home'}, 'FAILED', 1),
                 ({'ok': False, 'complete': False, 'status': 'otp_timeout',
                   'message': '[OTP_TIMEOUT] Không nhận được mã OTP mới.'}, 'FAILED', 1),
                 ({'ok': True, 'complete': False, 'ready_to_submit': True,
                   'status': 'ready_to_submit'}, 'READY_TO_SUBMIT', 0)]
        for result, status, exit_code in cases:
            with self.subTest(status=result['status']), tempfile.TemporaryDirectory() as directory:
                with patch.object(sys, 'argv', ['full', '--xlsx', 'unused.xlsx', '--row', '2', '--no-container']), \
                     patch.object(full_flow, 'ROOT_DIR', Path(directory)), \
                     patch.object(profile_reader, 'load_row', return_value=(profile, 2, 'Inputs')), \
                     patch('flow.build_otp_client', return_value=object()), \
                     patch.object(full_flow, 'prepare_account_proxy', return_value={'proxy_id': 'PX-test', 'proxy_used': 'test:8080', 'ip': '192.0.2.1'}), \
                     patch.object(runner, 'run_with_frida', return_value=result), \
                     patch.object(full_flow, 'write_row_status') as write, \
                     patch.object(full_flow, 'run_cmd_output') as crane_call, \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(full_flow.main(), exit_code)
                    self.assertEqual(write.call_args.args[3], status)
                    if result['status'] == 'otp_timeout':
                        self.assertEqual(write.call_args.args[4], result['message'])
                    self.assertEqual(list(Path(directory).rglob('input_*.json')), [])
                    crane_call.assert_not_called()

    def test_manually_skipped_otp_row_is_not_selected_by_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'inputs.xlsx'
            wb = full_flow.openpyxl.Workbook()
            ws = wb.active
            ws.title = 'Inputs'
            ws.append(['phone_number', 'status', 'message', 'frida_device_id'])
            ws.append(['07012345678', 'FAILED', 'OTP timed out', 'test-device'])
            wb.save(path)
            wb.close()
            full_flow.write_row_status(path, 'Inputs', 2, 'FAIL_NO_RETRY', '[OTP_TIMEOUT] Bỏ qua theo yêu cầu.')
            self.assertEqual(batch_flow.runnable_rows(path, 'Inputs', 0)[1], [])

    def test_failed_crane_result_never_starts_registration(self):
        profile = {'phone_number': '07012345678', 'account_password': 'TestOnly123',
                   'kana_sei': 'テスト', 'kana_mei': 'タロウ', 'birthday': '1999-02-03', 'pin': '0042'}
        with patch.object(sys, 'argv', ['full', '--xlsx', 'unused.xlsx', '--row', '2']), \
             patch.object(profile_reader, 'load_row', return_value=(profile, 2, 'Inputs')), \
             patch('flow.build_otp_client', return_value=object()), \
             patch.object(full_flow, 'prepare_account_proxy', return_value={'proxy_id': 'PX-test', 'proxy_used': 'test:8080', 'ip': '192.0.2.1'}), \
             patch.object(full_flow, 'run_cmd_output', return_value='{"ok": false}'), \
             patch.object(runner, 'run_with_frida') as run, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(full_flow.main(), 1)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
