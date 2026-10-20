import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from frida_stub import install_frida_stub

install_frida_stub()  # no-op when the real frida is installed
import airwallet_dom_runner as runner
import airwallet_batch_flow as batch
import powerselector


def confirmation(**overrides):
    result = {'bundle': powerselector.BUNDLE, 'topVC': 'UIAlertController',
              'alert': {'message': 'Confirm Respring', 'buttons': ['Respring', 'Cancel']}}
    result.update(overrides)
    return result


class PowerSelectorTests(unittest.TestCase):
    def test_only_exact_respring_prompt_is_accepted(self):
        self.assertTrue(powerselector.validate_confirmation(confirmation()))
        for data in (confirmation(bundle='other.app'),
                     confirmation(alert={'message': 'Confirm Reboot', 'buttons': ['Reboot', 'Cancel']}),
                     confirmation(alert={'message': 'Confirm Respring', 'buttons': ['Respring', 'Reboot']}),
                     confirmation(alert=None)):
            self.assertFalse(powerselector.validate_confirmation(data))

    def test_missing_tweak_stops_without_killing_any_process(self):
        device = Mock()
        device.enumerate_applications.return_value = []
        with self.assertRaisesRegex(RuntimeError, 'Chưa cài PowerSelector'):
            powerselector.request_respring(device)
        device.kill.assert_not_called()
        device.spawn.assert_not_called()

    def make_device(self, snapshots):
        device, session, script, api = Mock(), Mock(), Mock(), Mock()
        device.enumerate_applications.return_value = [SimpleNamespace(identifier=powerselector.BUNDLE, pid=88)]
        device.spawn.return_value = 99
        device.attach.return_value = session
        session.create_script.return_value = script
        script.exports_sync = api
        api.snapshot.side_effect = snapshots
        api.choose_alert.return_value = True
        return device, session, api

    def test_tweak_is_opened_and_only_respring_is_confirmed(self):
        device, session, api = self.make_device([{'topVC': 'PSController'}, confirmation()])
        with patch.object(powerselector, 'spawn_slot', return_value=contextlib.nullcontext()), \
             patch.object(powerselector, 'load_agent_source', return_value=''):
            powerselector.request_respring(device)
        device.kill.assert_called_once_with(88)  # Only the stale PowerSelector app, never SpringBoard.
        device.spawn.assert_called_once_with([powerselector.BUNDLE])
        device.resume.assert_called_once_with(99)
        api.open_respring_prompt.assert_called_once_with()
        api.choose_alert.assert_called_once_with('Respring', 'Confirm Respring')
        session.detach.assert_called_once()

    def test_unknown_prompt_is_preserved_without_confirmation(self):
        device, session, api = self.make_device([{'topVC': 'PSController'},
                                               confirmation(alert={'message': 'Confirm Reboot', 'buttons': ['Reboot', 'Cancel']})])
        with patch.object(powerselector, 'spawn_slot', return_value=contextlib.nullcontext()), \
             patch.object(powerselector, 'load_agent_source', return_value=''):
            with self.assertRaisesRegex(RuntimeError, 'Không khớp'):
                powerselector.request_respring(device)
        api.choose_alert.assert_not_called()
        session.detach.assert_called_once()

    def test_failed_script_load_resumes_spawn_and_detaches(self):
        device, session, api = self.make_device([])
        session.create_script.return_value.load.side_effect = RuntimeError('load failed')
        with patch.object(powerselector, 'spawn_slot', return_value=contextlib.nullcontext()), \
             patch.object(powerselector, 'load_agent_source', return_value=''):
            with self.assertRaisesRegex(RuntimeError, 'load failed'):
                powerselector.request_respring(device)
        device.resume.assert_called_once_with(99)
        session.detach.assert_called_once()


class RecoveryTests(unittest.TestCase):
    def test_changed_springboard_pid_is_required_before_wake(self):
        device = Mock()
        with patch.object(runner, 'springboard_pid', side_effect=[100, 101]), \
             patch('powerselector.request_respring') as request, \
             patch.object(runner.time, 'time', return_value=0), \
             patch.object(runner.time, 'sleep'), \
             patch.object(runner, 'wake_screen', return_value={'wake': 'unblank', 'net_back_sec': 10.4}) as wake, \
             contextlib.redirect_stderr(io.StringIO()):
            result = runner.respring_springboard(device, 0)
        self.assertTrue(result['ok'])
        self.assertTrue(result['net_ok'])
        self.assertEqual(result['method'], 'powerselector')
        self.assertEqual(result['springboard_new_pid'], 101)
        request.assert_called_once_with(device)
        wake.assert_called_once()
        device.kill.assert_not_called()

    def test_unchanged_pid_times_out_without_kill_fallback(self):
        device = Mock()
        with patch.object(runner, 'springboard_pid', return_value=100), \
             patch('powerselector.request_respring'), \
             patch.object(runner.time, 'time', side_effect=[0, 0, 0, 46]), \
             patch.object(runner.time, 'sleep'), \
             patch.object(runner, 'wake_screen') as wake, \
             contextlib.redirect_stderr(io.StringIO()):
            result = runner.respring_springboard(device, 0)
        self.assertFalse(result['ok'])
        self.assertFalse(result['net_ok'])
        device.kill.assert_not_called()
        wake.assert_not_called()

    def test_tweak_failure_never_falls_back_to_kill(self):
        device = Mock()
        with patch.object(runner, 'springboard_pid', return_value=100), \
             patch('powerselector.request_respring', side_effect=RuntimeError('missing tweak')):
            result = runner.respring_springboard(device, 0)
        self.assertFalse(result['ok'])
        device.kill.assert_not_called()

    def test_wake_attaches_springboard_without_launching_airwallet(self):
        device = Mock()
        api = device.attach.return_value.create_script.return_value.exports_sync
        api.wake.return_value = 'unblank'
        api.reachable.return_value = 'up:2'
        with patch.object(runner, 'springboard_pid', return_value=101), \
             contextlib.redirect_stderr(io.StringIO()):
            result = runner.wake_screen(device, 'jp.coinplus.app', wait_network=True)
        self.assertIsNotNone(result['net_back_sec'])
        device.attach.assert_called_once_with(101)
        device.spawn.assert_not_called()
        device.kill.assert_not_called()
        device.attach.return_value.detach.assert_called_once()

    def test_meminfo_uses_returned_page_size_and_preserves_model_comma(self):
        for page_size in (4096, 16384):
            with self.subTest(page_size=page_size):
                device = Mock()
                api = device.attach.return_value.create_script.return_value.exports_sync
                api.meminfo.return_value = f'ok:free=10,comp=200,ps={page_size},total=2147483648,model=iPhone10,1'
                with patch.object(runner, 'springboard_pid', return_value=101):
                    result = runner.read_meminfo(device)
                self.assertTrue(result['ok'])
                self.assertEqual(result['model'], 'iPhone10,1')
                self.assertEqual(result['total_mb'], 2048)
                self.assertEqual(result['compressor_mb'], round(200 * page_size / 1048576, 1))


class BatchRecoveryTests(unittest.TestCase):
    def run_batch(self, *, compressor, respring_ok, network_ok, every=0):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'placeholder.xlsx'
            path.touch()
            argv = ['batch', '--xlsx', str(path), '--respring-every', str(every),
                    '--respring-compressor-mb', '800', '--row-timeout-sec', '0',
                    '--device-start-gap-sec', '0', '--respring-cooldown-sec', '0']
            def run(cmd, **kwargs):
                if 'meminfo' in cmd:
                    data = {'ok': True, 'compressor_mb': compressor, 'free_mb': 20,
                            'total_mb': 1975.1, 'model': 'iPhone10,1'}
                elif 'respring' in cmd:
                    data = {'ok': respring_ok, 'net_ok': network_ok, 'method': 'powerselector'}
                else:
                    raise AssertionError('Unexpected command: ' + repr(cmd))
                return subprocess.CompletedProcess(cmd, 0, json.dumps(data), '')
            def popen(*args, **kwargs):
                process = Mock()
                process.stdout = ['row done\n']
                process.wait.return_value = 0
                return process
            with patch.object(sys, 'argv', argv), \
                 patch.object(batch, 'CONFIG', {'log_compressor': True}), \
                 patch('flow.build_otp_client', return_value=object()), \
                 patch.object(batch, 'resolve_device_ids', return_value=['test-device']), \
                 patch.object(batch, 'frida_python', return_value=sys.executable), \
                 patch.object(batch, 'runnable_rows', return_value=('Inputs', [
                     {'row': 2, 'device_id': 'test-device'}, {'row': 3, 'device_id': 'test-device'}])), \
                 patch.object(batch.subprocess, 'Popen', side_effect=popen) as start_row, \
                 patch.object(batch.subprocess, 'run', side_effect=run) as maintenance, \
                 contextlib.redirect_stdout(io.StringIO()):
                code = batch.main()
            return code, start_row.call_count, [call.args[0] for call in maintenance.call_args_list]

    def test_ram_pressure_runs_respring_between_rows(self):
        code, rows, calls = self.run_batch(compressor=900, respring_ok=True, network_ok=True)
        self.assertEqual((code, rows), (0, 2))
        self.assertEqual(sum('respring' in cmd for cmd in calls), 2)

    def test_row_count_also_uses_respring(self):
        code, rows, calls = self.run_batch(compressor=100, respring_ok=True, network_ok=True, every=1)
        self.assertEqual((code, rows), (0, 2))
        self.assertEqual(sum('respring' in cmd for cmd in calls), 2)

    def test_failed_respring_or_network_keeps_next_row_unstarted(self):
        for recovered, network in ((False, False), (True, False)):
            with self.subTest(recovered=recovered, network=network):
                code, rows, calls = self.run_batch(compressor=900, respring_ok=recovered, network_ok=network)
                self.assertEqual((code, rows), (1, 1))


if __name__ == '__main__':
    unittest.main()
