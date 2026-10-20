"""The iOS modal guard must report when it stops working.

Before this, `SpringBoardAlertGuard._loop` caught every exception and did
nothing with it. If the SpringBoard Frida session died, the guard silently
stopped declining permission modals while the run carried on as if it were
still protected, and the dump recorded nothing.
"""
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frida_stub import install_frida_stub

install_frida_stub()  # no-op when the real frida is installed

from airwallet_dom_runner import SpringBoardAlertGuard


def make_guard(sweeps) -> SpringBoardAlertGuard:
    """A guard with its Frida plumbing bypassed and a scripted sweep()."""
    guard = object.__new__(SpringBoardAlertGuard)
    guard.app_name = 'Demo'
    guard.interval = 0.001
    guard.session = None
    guard.script = None
    guard.tapped = []
    guard.failures = 0
    guard.max_failures = 5
    guard.degraded = False
    guard.last_error = ''
    guard._stop = threading.Event()
    guard._thread = None
    calls = {'n': 0}

    def sweep():
        calls['n'] += 1
        outcome = sweeps(calls['n'])
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    guard.sweep = sweep
    guard.calls = calls
    return guard


class AlertGuardDiagnosticsTests(unittest.TestCase):
    def test_a_healthy_guard_stays_undegraded(self):
        guard = make_guard(lambda n: 'none')
        thread = threading.Thread(target=guard._loop, daemon=True)
        thread.start()
        threading.Event().wait(0.1)
        guard._stop.set()
        thread.join(2)
        self.assertFalse(guard.degraded)
        self.assertEqual(guard.failures, 0)

    def test_transient_failures_are_logged_once_and_then_reset(self):
        def sweeps(number):
            if number in (1, 2):
                return RuntimeError('rpc hiccup')
            return 'none'

        guard = make_guard(sweeps)
        thread = threading.Thread(target=guard._loop, daemon=True)
        thread.start()
        threading.Event().wait(0.15)
        guard._stop.set()
        thread.join(2)
        self.assertFalse(guard.degraded, 'two failures followed by success is not fatal')
        self.assertEqual(guard.failures, 0, 'the counter resets after a good sweep')
        self.assertEqual(guard.last_error, 'rpc hiccup')

    def test_sustained_failure_marks_the_guard_degraded_and_stops_the_loop(self):
        guard = make_guard(lambda n: RuntimeError('session detached'))
        thread = threading.Thread(target=guard._loop, daemon=True)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive(), 'the loop must give up rather than spin forever')
        self.assertTrue(guard.degraded)
        self.assertGreaterEqual(guard.failures, guard.max_failures)
        self.assertEqual(guard.last_error, 'session detached')

    def test_degraded_state_is_reported_in_the_run_result(self):
        """The dump must carry the guard's state, not just stderr."""
        source = (ROOT / 'scripts' / 'airwallet_dom_runner.py').read_text(encoding='utf-8')
        self.assertIn('"alert_guard"', source)
        self.assertIn('"degraded": bool(guard.degraded)', source)
        self.assertIn('return finish(run_airwallet(ex, profile, args))', source)


if __name__ == '__main__':
    unittest.main()
