"""Adapter unit tests: one per failure mode the harness must handle.

Covered here: missing element, element outside the interactable area, screen
changed under the test, animation not finished, keyboard covering the element,
container not ready, timeout, unexpected repeat, undeclared alert, ambiguous
selector, secure-field verification, authorization gates.
"""
import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uitest.crane as crane
from uitest import Budget, Selector, UITestAdapter
from uitest.backends.mock import (
    FakeApp, FakeClock, FakeContainerManager, FakeElement, FakeScreen, MockBackend,
)
from uitest.crane import CraneContainerSession
from uitest.errors import (
    AmbiguousSelectorError,
    BusinessActionNotAuthorizedError,
    ContainerChangedError,
    ElementNotFoundError,
    ElementNotInteractableError,
    ExpectationFailedError,
    ExpectationFailedError as _ExpectationFailed,
    KeyboardObstructionError,
    NotSettledError,
    RepeatedActionError,
    ScreenChangedError,
    ScreenVerificationError,
    SessionClosedError,
    TargetNotAuthorizedError,
    UnexpectedAlertError,
    UnverifiableInputError,
)
from uitest.expectations import ElementEnabled, ElementValue, ScreenIs, ScreenSpec
from uitest.model import Alert, Rect
from uitest.policy import AlertPolicy, AlertRule, AuthorizedTarget

BUNDLE = 'com.example.demo'
TARGET = AuthorizedTarget(bundle_id=BUNDLE, environment='demo',
                          authorization_ref='DEMO-1', owner='qa@example.com')
BUDGET = Budget(timeout=2.0, poll_interval=0.25, settle_for=0.25, settle_samples=2,
                settle_timeout=2.0, infra_retries=2, infra_backoff=0.5, infra_backoff_max=1.0)

logging.getLogger('uitest').addHandler(logging.NullHandler())
logging.getLogger('uitest').propagate = False


def login_app() -> FakeApp:
    """Two screens: a login form and a landing screen."""
    login = FakeScreen('screen.login', [
        FakeElement('login.user', type='TextField', placeholder='user', value='',
                    frame=Rect(20, 120, 350, 44)),
        FakeElement('login.password', type='SecureTextField', value=None, protected=True,
                    frame=Rect(20, 180, 350, 44)),
        FakeElement('login.submit', type='Button', label='Sign in', enabled=False,
                    frame=Rect(20, 250, 350, 44)),
    ])
    landing = FakeScreen('screen.landing', [
        FakeElement('landing.title', type='StaticText', label='Welcome'),
    ])
    app = FakeApp(BUNDLE, [login, landing])
    app.navigates('screen.login', 'login.submit', 'screen.landing')

    def enable_submit(application: FakeApp, element: FakeElement, _text: str) -> None:
        screen = application.screens['screen.login']
        user = screen.find('login.user')
        submit = screen.find('login.submit')
        password_typed = any(ref == 'login.password' for ref, _ in application.typed)
        submit.enabled = bool(user.value) and password_typed

    app.on_type('screen.login', 'login.user', enable_submit)
    app.on_type('screen.login', 'login.password', enable_submit)
    return app


class AdapterCase(unittest.TestCase):
    def tearDown(self):
        crane._HELD.clear()

    def build(self, app=None, *, policy=None, container=None, read_faults=0, target=TARGET,
              budget=BUDGET):
        app = app if app is not None else login_app()
        clock = FakeClock()
        backend = MockBackend(app, clock=clock, read_faults=read_faults)
        adapter = UITestAdapter(backend, target=target, budget=budget,
                                alert_policy=policy or AlertPolicy(), container=container,
                                clock=clock, sleep=clock.sleep)
        return adapter, app, backend, clock


class ReadingTests(AdapterCase):
    def test_snapshot_reports_an_unknown_alert_instead_of_raising(self):
        app = login_app()
        app.screens['screen.login'].alert = Alert(identifier='alert.oops', title='Oops',
                                                  message='Something went wrong', buttons=('OK',))
        adapter, _app, _backend, _clock = self.build(app)
        snapshot = adapter.snapshot()
        self.assertIsNotNone(snapshot.alert)
        self.assertEqual(snapshot.alert.identifier, 'alert.oops')

    def test_snapshot_retries_a_transport_fault_then_succeeds(self):
        adapter, _app, backend, clock = self.build(read_faults=2)
        snapshot = adapter.snapshot()
        self.assertEqual(snapshot.screen_id, 'screen.login')
        self.assertEqual(clock.total_slept, 1.5)  # 0.5 + 1.0 backoff
        self.assertEqual(backend.capture_calls, 3)

    def test_snapshot_refuses_a_foreground_app_that_is_not_the_target(self):
        app = login_app()
        app.bundle_id = 'com.other.app'
        adapter, _app, _backend, _clock = self.build(app)
        with self.assertRaises(TargetNotAuthorizedError) as caught:
            adapter.snapshot()
        self.assertIn('com.other.app', str(caught.exception))

    def test_find_element_reports_absence_with_the_identifiers_it_did_see(self):
        adapter, _app, _backend, _clock = self.build()
        with self.assertRaises(ElementNotFoundError) as caught:
            adapter.find_element(Selector.by_id('login.missing'))
        self.assertIn('login.submit', str(caught.exception))

    def test_find_element_refuses_an_ambiguous_selector(self):
        screen = FakeScreen('screen.list', [
            FakeElement('row.delete', label='Delete', frame=Rect(20, 100, 350, 44)),
            FakeElement('row.delete', label='Delete', frame=Rect(20, 160, 350, 44)),
        ])
        adapter, _app, _backend, _clock = self.build(FakeApp(BUNDLE, [screen]))
        with self.assertRaises(AmbiguousSelectorError) as caught:
            adapter.find_element(Selector.by_id('row.delete'))
        self.assertIn('unique accessibility identifier', str(caught.exception))
        # index= is the deterministic escape hatch for a genuinely repeated control.
        self.assertEqual(adapter.find_element(Selector.by_id('row.delete', index=1)).frame.y, 160)

    def test_wait_until_visible_returns_once_the_element_appears(self):
        screen = FakeScreen('screen.loading', [
            FakeElement('spinner', type='ActivityIndicator', frame=Rect(180, 400, 30, 30)),
        ])
        app = FakeApp(BUNDLE, [screen])
        adapter, _app, _backend, _clock = self.build(app)
        reveal_at = 3

        original = app.capture

        def capture(clock):
            if app.reads == reveal_at:
                screen.elements.append(FakeElement('result', type='StaticText', label='Done'))
            return original(clock)

        app.capture = capture
        element = adapter.wait_until_visible(Selector.by_id('result'))
        self.assertEqual(element.label, 'Done')

    def test_wait_until_visible_times_out_with_the_precise_reason(self):
        """A timeout is re-derived into the real cause, not left as a bare timeout."""
        adapter, app, _backend, _clock = self.build()
        app.screens['screen.login'].find('login.user').visible = False
        with self.assertRaises(ElementNotInteractableError) as caught:
            adapter.wait_until_visible(Selector.by_id('login.user'), timeout=1.0)
        self.assertEqual(caught.exception.reason, 'not_visible')


class VerifyScreenTests(AdapterCase):
    def test_verifies_a_matching_screen(self):
        adapter, _app, _backend, _clock = self.build()
        spec = ScreenSpec('screen.login', required=(Selector.by_id('login.submit'),),
                          forbidden=(Selector.by_id('landing.title'),), name='Login')
        snapshot = adapter.verify_screen(spec)
        self.assertEqual(snapshot.screen_id, 'screen.login')

    def test_reports_the_exact_diff_on_mismatch(self):
        adapter, _app, _backend, _clock = self.build()
        spec = ScreenSpec('screen.login', required=(Selector.by_id('login.terms'),), name='Login')
        with self.assertRaises(ScreenVerificationError) as caught:
            adapter.verify_screen(spec, timeout=0.5)
        self.assertEqual(len(caught.exception.missing), 1)
        self.assertIn('login.terms', str(caught.exception))

    def test_reports_a_forbidden_element(self):
        adapter, _app, _backend, _clock = self.build()
        spec = ScreenSpec('screen.login', forbidden=(Selector.by_id('login.submit'),))
        with self.assertRaises(ScreenVerificationError) as caught:
            adapter.verify_screen(spec, timeout=0.5)
        self.assertEqual(len(caught.exception.unexpected), 1)


class TapTests(AdapterCase):
    def enabled_submit_app(self) -> FakeApp:
        app = login_app()
        app.screens['screen.login'].find('login.submit').enabled = True
        return app

    def test_tap_succeeds_only_after_its_post_condition_is_observed(self):
        adapter, app, _backend, _clock = self.build(self.enabled_submit_app())
        snapshot = adapter.tap_element(Selector.by_id('login.submit'),
                                       expect=ScreenIs('screen.landing'))
        self.assertEqual(snapshot.screen_id, 'screen.landing')
        self.assertEqual(app.taps, [('screen.login', 'login.submit')])
        self.assertEqual(adapter.history[-1].verb, 'tap')

    def test_tap_fails_when_nothing_happens(self):
        app = self.enabled_submit_app()
        app._tap_handlers.clear()  # the button dispatches but the app does not navigate
        adapter, _app, _backend, _clock = self.build(app)
        with self.assertRaises(ExpectationFailedError) as caught:
            adapter.tap_element(Selector.by_id('login.submit'),
                                expect=ScreenIs('screen.landing'), timeout=1.0)
        self.assertIn('post-condition was never observed', str(caught.exception))

    def test_tap_refuses_a_disabled_element(self):
        adapter, _app, _backend, _clock = self.build()  # submit starts disabled
        with self.assertRaises(ElementNotInteractableError) as caught:
            adapter.tap_element(Selector.by_id('login.submit'),
                                expect=ScreenIs('screen.landing'), timeout=0.5)
        self.assertEqual(caught.exception.reason, 'disabled')

    def test_tap_refuses_an_element_outside_the_interactable_area(self):
        app = self.enabled_submit_app()
        app.screens['screen.login'].find('login.submit').frame = Rect(20, 900, 350, 44)
        adapter, _app, backend, _clock = self.build(app)
        with self.assertRaises(ElementNotInteractableError) as caught:
            adapter.tap_element(Selector.by_id('login.submit'),
                                expect=ScreenIs('screen.landing'), timeout=0.5)
        self.assertEqual(caught.exception.reason, 'outside_interactable_area')
        self.assertEqual(backend.app.taps, [], 'nothing was tapped')

    def test_tap_scrolls_an_off_screen_element_into_view_once_then_proceeds(self):
        app = self.enabled_submit_app()
        submit = app.screens['screen.login'].find('login.submit')
        submit.frame = Rect(20, 900, 350, 44)

        original = app.scroll_into_view

        def scroll(ref):
            original(ref)
            submit.frame = Rect(20, 400, 350, 44)  # the scroll brings it in

        app.scroll_into_view = scroll
        adapter, _app, _backend, _clock = self.build(app)
        snapshot = adapter.tap_element(Selector.by_id('login.submit'),
                                       expect=ScreenIs('screen.landing'))
        self.assertEqual(snapshot.screen_id, 'screen.landing')
        self.assertEqual(app.scrolled, ['login.submit'], 'exactly one recovery attempt')
        self.assertEqual(adapter.history[-1].recovered, 'outside_interactable_area')

    def test_tap_refuses_an_element_under_the_keyboard(self):
        app = self.enabled_submit_app()
        app.screens['screen.login'].keyboard = True
        app.screens['screen.login'].find('login.submit').frame = Rect(20, 600, 350, 44)
        adapter, _app, _backend, _clock = self.build(app)
        with self.assertRaises(KeyboardObstructionError) as caught:
            adapter.tap_element(Selector.by_id('login.submit'),
                                expect=ScreenIs('screen.landing'), timeout=0.5, recover=False)
        self.assertEqual(caught.exception.reason, 'keyboard_obstruction')

    def test_tap_dismisses_the_keyboard_as_a_bounded_recovery(self):
        app = self.enabled_submit_app()
        app.screens['screen.login'].keyboard = True
        app.screens['screen.login'].find('login.submit').frame = Rect(20, 600, 350, 44)
        adapter, _app, _backend, _clock = self.build(app)
        snapshot = adapter.tap_element(Selector.by_id('login.submit'),
                                       expect=ScreenIs('screen.landing'))
        self.assertEqual(snapshot.screen_id, 'screen.landing')
        self.assertEqual(app.keyboard_dismissals, 1)

    def test_tap_waits_for_an_animation_to_finish(self):
        app = self.enabled_submit_app()
        app.moving_reads = 4
        adapter, _app, _backend, clock = self.build(app)
        adapter.tap_element(Selector.by_id('login.submit'), expect=ScreenIs('screen.landing'))
        self.assertGreater(clock.total_slept, 0.0, 'the settle gate waited')
        self.assertEqual(app.taps, [('screen.login', 'login.submit')])

    def test_tap_refuses_to_act_while_the_screen_keeps_moving(self):
        app = self.enabled_submit_app()
        app.moving_reads = 10 ** 6
        adapter, _app, _backend, _clock = self.build(app)
        with self.assertRaises(NotSettledError):
            adapter.tap_element(Selector.by_id('login.submit'), expect=ScreenIs('screen.landing'))
        self.assertEqual(app.taps, [])

    def test_tap_stops_when_the_screen_changes_before_dispatch(self):
        app = self.enabled_submit_app()
        adapter, _app, _backend, _clock = self.build(app)
        # Simulate a late push notification / deep link landing in the window
        # between the readiness check and the dispatch.
        original_confirm = adapter._confirm_unchanged

        def confirm(selector, settled, *, recover):
            app.goto('screen.landing')
            return original_confirm(selector, settled, recover=recover)

        adapter._confirm_unchanged = confirm
        with self.assertRaises(ScreenChangedError) as caught:
            adapter.tap_element(Selector.by_id('login.submit'), expect=ScreenIs('screen.landing'))
        self.assertIn('Nothing was tapped', str(caught.exception))
        self.assertEqual(app.taps, [])
        self.assertIsNotNone(caught.exception.expected)

    def test_tap_refuses_an_unexpected_repeat_on_the_same_screen(self):
        app = login_app()
        app._tap_handlers.clear()
        app.screens['screen.login'].find('login.submit').enabled = True
        adapter, _app, _backend, _clock = self.build(app)
        # Make the tap "work" by an expectation that is already true, so the
        # first call succeeds and the screen is unchanged afterwards.
        expectation = ScreenIs('screen.login')
        adapter.tap_element(Selector.by_id('login.submit'), expect=expectation)
        with self.assertRaises(RepeatedActionError) as caught:
            adapter.tap_element(Selector.by_id('login.submit'), expect=expectation)
        self.assertIn('double-submit', str(caught.exception))
        self.assertEqual(len(app.taps), 1)

    def test_allow_repeat_permits_a_genuinely_repeatable_control(self):
        keypad = FakeScreen('screen.pin', [
            FakeElement('pin.1', label='1', frame=Rect(20, 300, 80, 80)),
            FakeElement('pin.dots', type='StaticText', value='', frame=Rect(20, 100, 350, 20)),
        ])
        app = FakeApp(BUNDLE, [keypad])

        def press(application, _element):
            dots = application.screens['screen.pin'].find('pin.dots')
            dots.value = (dots.value or '') + '*'

        app.on_tap('screen.pin', 'pin.1', press)
        adapter, _app, _backend, _clock = self.build(app)
        for index in range(1, 4):
            adapter.tap_element(Selector.by_id('pin.1'), allow_repeat=True,
                                expect=ElementValue(Selector.by_id('pin.dots'), '*' * index))
            self.assertEqual(len(app.taps), index)

    def test_tap_halts_on_an_undeclared_alert(self):
        app = self.enabled_submit_app()
        app.screens['screen.login'].alert = Alert(identifier='alert.unknown', title='Notice',
                                                  message='Unmapped', buttons=('OK', 'Cancel'))
        adapter, _app, _backend, _clock = self.build(app)
        with self.assertRaises(UnexpectedAlertError) as caught:
            adapter.tap_element(Selector.by_id('login.submit'), expect=ScreenIs('screen.landing'))
        self.assertIn('left on screen for review', str(caught.exception))
        self.assertEqual(app.taps, [])
        self.assertIsNotNone(app.screens['screen.login'].alert, 'alert untouched')

    def test_declared_alert_is_dismissed_through_its_named_button(self):
        app = self.enabled_submit_app()
        app.screens['screen.login'].alert = Alert(identifier='alert.terms', title='Terms',
                                                  message='Accept later?', buttons=('Later', 'Accept'))
        policy = AlertPolicy((AlertRule(name='terms', identifier='alert.terms', dismiss_with='Later'),))
        adapter, _app, _backend, _clock = self.build(app, policy=policy)
        from uitest.expectations import NoAlert
        adapter.dismiss_alert(expect=NoAlert())
        self.assertEqual(app.alert_taps, ['Later'])

    def test_dismiss_refuses_a_button_the_alert_does_not_offer(self):
        app = self.enabled_submit_app()
        app.screens['screen.login'].alert = Alert(identifier='alert.terms', buttons=('Accept',))
        policy = AlertPolicy((AlertRule(name='terms', identifier='alert.terms', dismiss_with='Later'),))
        adapter, _app, _backend, _clock = self.build(app, policy=policy)
        from uitest.expectations import NoAlert
        with self.assertRaises(UnexpectedAlertError):
            adapter.dismiss_alert(expect=NoAlert())
        self.assertEqual(app.alert_taps, [])

    def test_two_matching_rules_count_as_unrecognised(self):
        policy = AlertPolicy((
            AlertRule(name='a', title='Notice', dismiss_with='OK'),
            AlertRule(name='b', title='Notice', dismiss_with='Cancel'),
        ))
        self.assertIsNone(policy.classify(Alert(title='Notice', buttons=('OK', 'Cancel'))))


class TypeTextTests(AdapterCase):
    def test_types_and_verifies_by_reading_the_field_back(self):
        adapter, app, _backend, _clock = self.build()
        adapter.type_text(Selector.by_id('login.user'), 'tester')
        self.assertEqual(app.typed, [('login.user', 'tester')])
        self.assertEqual(app.screens['screen.login'].find('login.user').value, 'tester')

    def test_clears_before_typing_by_default(self):
        adapter, app, _backend, _clock = self.build()
        app.screens['screen.login'].find('login.user').value = 'stale'
        adapter.type_text(Selector.by_id('login.user'), 'tester')
        self.assertEqual(app.cleared, ['login.user'])

    def test_secure_field_requires_an_observable_expectation(self):
        adapter, _app, _backend, _clock = self.build()
        with self.assertRaises(UnverifiableInputError) as caught:
            adapter.type_text(Selector.by_id('login.password'), 'hunter2')
        self.assertIn('cannot be read back', str(caught.exception))

    def test_secure_field_verified_through_a_side_effect(self):
        adapter, app, _backend, _clock = self.build()
        adapter.type_text(Selector.by_id('login.user'), 'tester')
        adapter.type_text(Selector.by_id('login.password'), 'hunter2', secret=True,
                          expect=ElementEnabled(Selector.by_id('login.submit'), True))
        self.assertTrue(app.screens['screen.login'].find('login.submit').enabled)

    def test_secret_values_are_scrubbed_from_errors(self):
        app = login_app()

        def uppercase(_application, element, text):
            element.value = text.upper()  # the field transforms what was typed

        app.on_type('screen.login', 'login.user', uppercase)
        adapter, _app, _backend, _clock = self.build(app)
        with self.assertRaises(_ExpectationFailed) as caught:
            adapter.type_text(Selector.by_id('login.user'), 'sup3rsecret', secret=True,
                              expect=ElementValue(Selector.by_id('login.user'), 'sup3rsecret'),
                              timeout=0.5)
        self.assertNotIn('sup3rsecret', str(caught.exception))
        self.assertIn('[redacted]', str(caught.exception))

    def test_typing_the_same_value_twice_is_refused(self):
        adapter, _app, _backend, _clock = self.build()
        adapter.type_text(Selector.by_id('login.user'), 'tester')
        with self.assertRaises(RepeatedActionError):
            adapter.type_text(Selector.by_id('login.user'), 'tester')


class AuthorizationTests(AdapterCase):
    def test_production_target_cannot_be_declared(self):
        with self.assertRaises(TargetNotAuthorizedError):
            AuthorizedTarget(bundle_id='jp.example.prod', environment='production',
                             authorization_ref='X', owner='y')

    def test_authorization_reference_and_owner_are_mandatory(self):
        with self.assertRaises(TargetNotAuthorizedError):
            AuthorizedTarget(bundle_id=BUNDLE, environment='demo', authorization_ref=' ', owner='y')
        with self.assertRaises(TargetNotAuthorizedError):
            AuthorizedTarget(bundle_id=BUNDLE, environment='demo', authorization_ref='X', owner='')

    def test_business_write_refused_unless_the_target_permits_it(self):
        app = login_app()
        app.screens['screen.login'].find('login.submit').enabled = True
        adapter, _app, _backend, _clock = self.build(app)
        with self.assertRaises(BusinessActionNotAuthorizedError) as caught:
            adapter.tap_element(Selector.by_id('login.submit'), expect=ScreenIs('screen.landing'),
                                business_write=True)
        self.assertIn('allow_business_writes=False', str(caught.exception))
        self.assertEqual(app.taps, [])

    def test_business_write_allowed_on_a_target_that_permits_it(self):
        target = AuthorizedTarget(bundle_id=BUNDLE, environment='sandbox',
                                  authorization_ref='DEMO-2', owner='qa@example.com',
                                  allow_business_writes=True)
        app = login_app()
        app.screens['screen.login'].find('login.submit').enabled = True
        adapter, _app, _backend, _clock = self.build(app, target=target)
        adapter.tap_element(Selector.by_id('login.submit'), expect=ScreenIs('screen.landing'),
                            business_write=True)
        self.assertEqual(len(app.taps), 1)


class ContainerIntegrationTests(AdapterCase):
    def test_container_not_ready_stops_before_any_action(self):
        manager = FakeContainerManager(BUNDLE, ('c1', 'c2'), active='c2')
        session = CraneContainerSession(manager, BUNDLE, 'c1')
        from uitest.errors import ContainerNotReadyError
        with self.assertRaises(ContainerNotReadyError):
            session.open()

    def test_mid_test_container_switch_aborts_the_next_action(self):
        manager = FakeContainerManager(BUNDLE, ('c1', 'c2'), active='c1')
        session = CraneContainerSession(manager, BUNDLE, 'c1')
        session.open()
        app = login_app()
        app.container_id = 'c1'   # the snapshot stamp must match the bound container
        app.screens['screen.login'].find('login.submit').enabled = True
        adapter, _app, _backend, _clock = self.build(app, container=session)
        adapter.type_text(Selector.by_id('login.user'), 'tester')
        manager.active = 'c2'
        with self.assertRaises(ContainerChangedError):
            adapter.tap_element(Selector.by_id('login.submit'), expect=ScreenIs('screen.landing'))
        self.assertEqual(app.taps, [])
        adapter.close()

    def test_snapshot_container_stamp_is_verified_against_the_bound_container(self):
        """Two independent sources must agree: Crane's view and the snapshot's stamp."""
        manager = FakeContainerManager(BUNDLE, ('c1', 'c2'), active='c1')
        session = CraneContainerSession(manager, BUNDLE, 'c1')
        session.open()
        app = login_app()
        app.container_id = 'c2'   # backend stamps a different container than Crane reports
        adapter, _app, _backend, _clock = self.build(app, container=session)
        with self.assertRaises(ContainerChangedError) as caught:
            adapter.snapshot()
        self.assertIn('does not belong to the container under test', str(caught.exception))
        adapter.close()

    def test_an_unstamped_snapshot_is_allowed(self):
        """No container provider wired up is not a mismatch; it is just no evidence."""
        manager = FakeContainerManager(BUNDLE, ('c1',), active='c1')
        session = CraneContainerSession(manager, BUNDLE, 'c1')
        session.open()
        app = login_app()
        app.container_id = ''
        adapter, _app, _backend, _clock = self.build(app, container=session)
        self.assertEqual(adapter.snapshot().container_id, '')
        adapter.close()

    def test_container_id_is_recorded_with_each_action(self):
        manager = FakeContainerManager(BUNDLE, ('c1',), active='c1')
        session = CraneContainerSession(manager, BUNDLE, 'c1')
        session.open()
        app = login_app()
        app.container_id = 'c1'
        adapter, _app, _backend, _clock = self.build(app, container=session)
        adapter.type_text(Selector.by_id('login.user'), 'tester')
        self.assertEqual(adapter.history[-1].container_id, 'c1')
        adapter.close()

    def test_close_releases_backend_and_container(self):
        manager = FakeContainerManager(BUNDLE, ('c1',), active='c1')
        session = CraneContainerSession(manager, BUNDLE, 'c1')
        session.open()
        adapter, app, _backend, _clock = self.build(container=session)
        adapter.close()
        adapter.close()  # idempotent
        self.assertTrue(app.closed)
        self.assertEqual(manager.detached, 1)
        with self.assertRaises(SessionClosedError):
            adapter.snapshot()


def executable_code(path: Path) -> str:
    """Source with comments and string literals removed.

    The modules *document* the APIs they deliberately avoid, so a naive text
    scan would flag its own prose. This keeps the check on real code.
    """
    import io
    import tokenize

    with io.StringIO(path.read_text(encoding='utf-8')) as handle:
        return ' '.join(
            token.string
            for token in tokenize.generate_tokens(handle.readline)
            if token.type not in (tokenize.COMMENT, tokenize.STRING)
        )


class NoForbiddenSurfaceTests(unittest.TestCase):
    def test_adapter_offers_no_coordinate_or_target_action_path(self):
        """The package must not reach for UIKit dispatch or bare coordinates."""
        root = Path(__file__).resolve().parents[1] / 'uitest'
        files = sorted(root.rglob('*.py'))
        self.assertGreater(len(files), 5, 'the scan found no modules')
        for path in files:
            code = executable_code(path)
            for forbidden in ('sendActionsForControlEvents', 'insertText_', 'becomeFirstResponder',
                              '_dismissWithAction', 'tap_coordinate', 'tap_point', 'objc_msgSend'):
                self.assertNotIn(forbidden, code,
                                 f'{forbidden} must not appear in {path.name}')


if __name__ == '__main__':
    unittest.main()
