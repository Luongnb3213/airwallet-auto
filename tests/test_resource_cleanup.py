"""Resource cleanup on failure paths: spawned processes, file descriptors, sessions.

Three defects, one theme -- a failure partway through setup left something
behind:

* N1 `driver.connect()` spawned the app, failed to attach, and never killed it.
* N2 `spawn_throttle.spawn_slot()` leaked a file descriptor when the lock call
  raised anything other than OSError.
* N3 `main.py` / `airwallet_dom_runner.run_with_frida` ran cleanup steps
  unguarded in `finally`, so a raising `guard.close()` skipped
  `session.detach()`.
"""
import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frida_stub import install_frida_stub

install_frida_stub()  # no-op when the real frida is installed

import driver
import spawn_throttle


def make_device(running_pid=0, spawn_pid=42):
    """A frida device double that records the order of operations."""
    events = []
    device, session, script = Mock(), Mock(), Mock()
    device.enumerate_applications.return_value = [
        SimpleNamespace(identifier=driver.BUNDLE, pid=running_pid)
    ]
    device.spawn.side_effect = lambda args: (events.append('spawn') or spawn_pid)
    device.resume.side_effect = lambda pid: events.append('resume')
    device.attach.side_effect = lambda pid: (events.append('attach') or session)
    device.kill.side_effect = lambda pid: events.append(f'kill:{pid}')
    session.create_script.return_value = script
    session.detach.side_effect = lambda: events.append('detach')
    script.load.side_effect = lambda: events.append('load')
    return device, session, script, events


class SpawnedProcessCleanupTests(unittest.TestCase):
    """N1: connect() returns a working session or leaves no trace."""

    @contextlib.contextmanager
    def patched(self, device):
        with patch.object(driver.frida, 'get_device', return_value=device), \
             patch.object(driver, 'load_agent_source', return_value=''), \
             patch.object(driver.time, 'sleep'):
            yield

    def test_attach_failure_kills_the_app_we_spawned(self):
        device, _session, _script, events = make_device()
        device.attach.side_effect = RuntimeError('attach failed')
        with self.patched(device):
            with self.assertRaisesRegex(RuntimeError, 'attach failed'):
                driver.connect('test-device', 0)
        self.assertIn('kill:42', events, 'a process we spawned must not be orphaned')
        device.kill.assert_called_once_with(42)

    def test_script_failure_also_kills_the_app_we_spawned(self):
        device, session, script, events = make_device()
        script.load.side_effect = RuntimeError('load failed')
        with self.patched(device):
            with self.assertRaisesRegex(RuntimeError, 'load failed'):
                driver.connect('test-device', 0)
        self.assertEqual(events, ['spawn', 'resume', 'attach', 'detach', 'kill:42'])
        session.detach.assert_called_once()
        device.resume.assert_called_once_with(42)   # never resumed twice

    def test_an_already_running_app_is_never_killed(self):
        device, _session, script, events = make_device(running_pid=77)
        script.load.side_effect = RuntimeError('load failed')
        with self.patched(device):
            with self.assertRaisesRegex(RuntimeError, 'load failed'):
                driver.connect('test-device', 0)
        device.kill.assert_not_called()
        self.assertNotIn('spawn', events, 'the app was already running')

    def test_resume_failure_still_kills_the_spawned_app(self):
        device, _session, _script, events = make_device()
        device.resume.side_effect = RuntimeError('resume failed')
        with self.patched(device):
            with self.assertRaisesRegex(RuntimeError, 'resume failed'):
                driver.connect('test-device', 0)
        self.assertIn('kill:42', events)

    def test_cleanup_failure_does_not_mask_the_original_error(self):
        device, session, script, _events = make_device()
        script.load.side_effect = RuntimeError('load failed')
        session.detach.side_effect = RuntimeError('detach blew up')
        device.kill.side_effect = RuntimeError('kill blew up')
        with self.patched(device):
            with self.assertRaisesRegex(RuntimeError, 'load failed'):
                driver.connect('test-device', 0)

    def test_success_path_kills_nothing(self):
        device, session, _script, events = make_device()
        with self.patched(device):
            result = driver.connect('test-device', 0)
        self.assertIs(result[1], session)
        device.kill.assert_not_called()
        self.assertEqual(events, ['spawn', 'resume', 'attach', 'load'])


class SlotDescriptorTests(unittest.TestCase):
    """N2: an unexpected lock error must not leak the descriptor."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._previous = os.environ.get('AIRWALLET_SPAWN_SLOT_DIR')
        os.environ['AIRWALLET_SPAWN_SLOT_DIR'] = self._tmp.name

    def tearDown(self):
        if self._previous is None:
            os.environ.pop('AIRWALLET_SPAWN_SLOT_DIR', None)
        else:
            os.environ['AIRWALLET_SPAWN_SLOT_DIR'] = self._previous
        self._tmp.cleanup()

    def open_descriptors(self) -> set[int]:
        """Probe which low descriptors are live, by attempting to dup them."""
        live = set()
        for number in range(3, 256):
            try:
                os.close(os.dup(number))
            except OSError:
                continue
            live.add(number)
        return live

    def test_unexpected_lock_error_closes_the_descriptor(self):
        before = self.open_descriptors()
        with patch.object(spawn_throttle, '_try_lock', side_effect=ValueError('bad fd')):
            with self.assertRaises(ValueError):
                with spawn_throttle.spawn_slot(slots=1, timeout=1.0):
                    pass
        after = self.open_descriptors()
        self.assertEqual(after - before, set(), 'the slot descriptor leaked')

    def test_busy_slots_fall_through_without_leaking(self):
        """Every slot held elsewhere: fail-open after the timeout, no leak."""
        before = self.open_descriptors()
        with patch.object(spawn_throttle, '_try_lock', side_effect=OSError('busy')):
            with contextlib.redirect_stderr(io.StringIO()):
                with spawn_throttle.spawn_slot(slots=2, timeout=0.05, poll=0.01,
                                               label='test'):
                    pass
        after = self.open_descriptors()
        self.assertEqual(after - before, set())

    def test_normal_acquire_and_release_leaks_nothing(self):
        before = self.open_descriptors()
        with spawn_throttle.spawn_slot(slots=1, timeout=1.0):
            pass
        after = self.open_descriptors()
        self.assertEqual(after - before, set())


class FinallyOrderingTests(unittest.TestCase):
    """N3: independent cleanup steps must not be able to skip each other."""

    def test_main_detaches_the_session_when_the_body_raises(self):
        """main.py's finally must run on the failure path, not just on success."""
        import main as entry

        session = Mock()
        device = SimpleNamespace(id='d', name='Device')
        script = Mock()
        script.exports_sync.snapshot.return_value = {'topVC': 'X'}

        argv = ['main.py', 'snapshot', '--config', str(ROOT / 'does-not-exist.json')]
        with patch.object(sys, 'argv', argv), \
             patch.object(entry.driver, 'connect',
                          return_value=(device, session, script, [])), \
             patch.object(entry.driver, 'pretty', side_effect=RuntimeError('render failed')), \
             contextlib.redirect_stderr(io.StringIO()), \
             contextlib.redirect_stdout(io.StringIO()):
            code = entry.main()

        self.assertEqual(code, 1)
        session.detach.assert_called_once()

    def test_main_reports_a_failing_cleanup_instead_of_swallowing_it(self):
        import main as entry

        session = Mock()
        session.detach.side_effect = RuntimeError('detach blew up')
        device = SimpleNamespace(id='d', name='Device')
        script = Mock()
        script.exports_sync.snapshot.return_value = {'topVC': 'X'}

        argv = ['main.py', 'snapshot', '--config', str(ROOT / 'does-not-exist.json')]
        errors = io.StringIO()
        with patch.object(sys, 'argv', argv), \
             patch.object(entry.driver, 'connect',
                          return_value=(device, session, script, [])), \
             patch.object(entry.driver, 'pretty', side_effect=RuntimeError('render failed')), \
             contextlib.redirect_stderr(errors), \
             contextlib.redirect_stdout(io.StringIO()):
            code = entry.main()

        self.assertEqual(code, 1)
        self.assertIn('Không dọn được', errors.getvalue(),
                      'a failing cleanup step must be reported, not swallowed')

    def test_both_entry_points_guard_each_cleanup_step(self):
        """Structural guard: neither finally may call cleanup bare."""
        main_source = (ROOT / 'main.py').read_text(encoding='utf-8')
        self.assertIn('Không dọn được', main_source)
        self.assertNotIn('        if guard is not None:\n            guard.close()\n',
                         main_source)

        dom_source = (ROOT / 'scripts' / 'airwallet_dom_runner.py').read_text(encoding='utf-8')
        self.assertIn('Không detach được phiên Frida', dom_source)
        self.assertNotIn('        if guard:\n            guard.close()\n        session.detach()',
                         dom_source)

    def test_dom_runner_detaches_even_if_the_guard_raises(self):
        import airwallet_dom_runner as runner

        session = Mock()
        guard = Mock()
        guard.close.side_effect = RuntimeError('guard teardown failed')
        device = Mock()
        script = Mock()
        script.exports_sync.snapshot.return_value = {'topVC': 'X'}
        args = runner.build_parser().parse_args(['--action', 'screen'])

        with patch.object(runner, 'import_frida', return_value=object()), \
             patch.object(runner, 'resolve_frida_device', return_value=device), \
             patch.object(runner, 'attach_or_launch', return_value=(session, False)), \
             patch.object(runner, 'start_alert_guard', return_value=guard), \
             patch.object(runner, 'load_agent', return_value=''), \
             patch.object(runner.time, 'sleep'), \
             contextlib.redirect_stderr(io.StringIO()):
            session.create_script.return_value = script
            runner.run_with_frida(args)

        session.detach.assert_called_once()


if __name__ == '__main__':
    unittest.main()
