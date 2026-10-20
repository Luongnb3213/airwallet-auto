"""Timing policy: bounded timeouts, settling, read-only backoff."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from uitest.backends.mock import FakeApp, FakeClock, FakeElement, FakeScreen, MockBackend
from uitest.errors import InfrastructureError, NotSettledError, WaitTimeoutError
from uitest.waits import Budget, Deadline, Waiter, backoff_delays, retry_read


def app_with_moving_screen(moving_reads: int) -> FakeApp:
    screen = FakeScreen('home', [FakeElement('home.title', type='StaticText', label='Home')])
    app = FakeApp('com.example.demo', [screen])
    app.moving_reads = moving_reads
    return app


class BudgetTests(unittest.TestCase):
    def test_rejects_non_positive_and_unobservable_settings(self):
        for field, value in (('timeout', 0), ('poll_interval', -1), ('settle_for', 0),
                             ('settle_timeout', 0), ('infra_backoff', 0), ('infra_backoff_max', -2)):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    Budget(**{field: value})
        with self.assertRaises(ValueError):
            Budget(settle_samples=1)  # cannot observe stability from one sample
        with self.assertRaises(ValueError):
            Budget(infra_retries=-1)

    def test_with_timeout_overrides_only_the_timeout(self):
        budget = Budget(timeout=10, poll_interval=0.1)
        narrowed = budget.with_timeout(2)
        self.assertEqual(narrowed.timeout, 2)
        self.assertEqual(narrowed.poll_interval, 0.1)
        self.assertEqual(budget.with_timeout(None), budget)


class DeadlineTests(unittest.TestCase):
    def test_expires_exactly_at_the_limit(self):
        clock = FakeClock()
        deadline = Deadline(1.0, clock=clock)
        self.assertFalse(deadline.expired)
        clock.sleep(0.5)
        self.assertAlmostEqual(deadline.remaining, 0.5)
        clock.sleep(0.5)
        self.assertTrue(deadline.expired)


class BackoffTests(unittest.TestCase):
    def test_delays_grow_then_cap_without_jitter(self):
        self.assertEqual(list(backoff_delays(5, 0.5, 2.0)), [0.5, 1.0, 2.0, 2.0, 2.0])
        self.assertEqual(list(backoff_delays(0, 0.5, 2.0)), [])

    def test_retry_read_recovers_within_budget(self):
        clock = FakeClock()
        app = app_with_moving_screen(0)
        backend = MockBackend(app, clock=clock, read_faults=2)
        budget = Budget(infra_retries=3, infra_backoff=0.5, infra_backoff_max=2.0)
        retries = []
        snapshot = retry_read(backend.capture, budget=budget, sleep=clock.sleep,
                              on_retry=lambda attempt, delay, error: retries.append((attempt, delay)))
        self.assertEqual(snapshot.screen_id, 'home')
        self.assertEqual(retries, [(1, 0.5), (2, 1.0)])
        self.assertEqual(clock.total_slept, 1.5)

    def test_retry_read_gives_up_and_names_the_budget(self):
        clock = FakeClock()
        backend = MockBackend(app_with_moving_screen(0), clock=clock, read_faults=99)
        budget = Budget(infra_retries=2, infra_backoff=0.5, infra_backoff_max=1.0)
        with self.assertRaises(InfrastructureError) as caught:
            retry_read(backend.capture, budget=budget, sleep=clock.sleep)
        self.assertIn('after 2 retries', str(caught.exception))
        self.assertEqual(backend.capture_calls, 3)  # attempts = retries + 1

    def test_backoff_is_never_applied_to_a_mutating_call(self):
        """Guard against regressions: tap/type must not be routed through retry_read."""
        source = (Path(__file__).resolve().parents[1] / 'uitest' / 'adapter.py').read_text(encoding='utf-8')
        for mutator in ('self._backend.tap(', 'self._backend.type_text(', 'self._backend.clear_text('):
            index = source.index(mutator)
            window = source[max(0, index - 400):index]
            self.assertNotIn('retry_read', window,
                             f'{mutator} appears inside a retry window; a retried tap can double-submit.')


class WaiterTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()

    def waiter(self, app, budget=None):
        backend = MockBackend(app, clock=self.clock)
        return Waiter(backend.capture, budget=budget or Budget(timeout=2.0, poll_interval=0.25,
                                                               settle_for=0.3, settle_timeout=2.0),
                      clock=self.clock, sleep=self.clock.sleep)

    def test_poll_returns_as_soon_as_the_condition_holds(self):
        app = app_with_moving_screen(0)
        waiter = self.waiter(app)
        snapshot = waiter.poll(lambda snap: snap.screen_id == 'home', description='home')
        self.assertEqual(snapshot.screen_id, 'home')
        self.assertEqual(self.clock.total_slept, 0.0, 'no sleep when already satisfied')

    def test_poll_is_bounded_and_reports_elapsed(self):
        waiter = self.waiter(app_with_moving_screen(0))
        with self.assertRaises(WaitTimeoutError) as caught:
            waiter.poll(lambda snap: False, description='an impossible condition')
        self.assertLessEqual(caught.exception.elapsed, 2.25)
        self.assertIn('an impossible condition', str(caught.exception))

    def test_poll_aborts_early_when_inspect_raises(self):
        waiter = self.waiter(app_with_moving_screen(0))

        def inspect(_snapshot):
            raise RuntimeError('stop now')

        with self.assertRaises(RuntimeError):
            waiter.poll(lambda snap: False, description='x', inspect=inspect)
        self.assertEqual(self.clock.total_slept, 0.0, 'aborts without burning the timeout')

    def test_settle_waits_out_an_animation_then_returns(self):
        app = app_with_moving_screen(4)
        waiter = self.waiter(app)
        snapshot = waiter.settle()
        self.assertEqual(snapshot.screen_id, 'home')
        self.assertGreater(app.reads, 4, 'kept reading while frames moved')
        self.assertGreater(self.clock.total_slept, 0.0)

    def test_settle_fails_when_the_screen_never_stops(self):
        app = app_with_moving_screen(10 ** 6)
        waiter = self.waiter(app)
        with self.assertRaises(NotSettledError) as caught:
            waiter.settle(description='the submit button')
        self.assertIn('still changing', str(caught.exception))
        self.assertIn('the submit button', str(caught.exception))
        self.assertLessEqual(caught.exception.elapsed, 2.25)


if __name__ == '__main__':
    unittest.main()
