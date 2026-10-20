"""Integration tests with mocks.

Two levels:

* :class:`DemoFlowTests` runs a complete demo-app journey through the real
  adapter, container session and timing policy, against the in-memory
  :class:`~uitest.backends.mock.FakeApp`.
* :class:`WebDriverAgentMappingTests` drives the WDA backend against an HTTP
  double, checking the XCUITest attribute mapping and the request it issues for
  each action.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uitest.crane as crane
from uitest import Budget, Selector, UITestAdapter
from uitest.backends.mock import (
    FakeApp, FakeClock, FakeContainerManager, FakeElement, FakeScreen, MockBackend,
)
from uitest.backends.wda import WebDriverAgentBackend
from uitest.crane import CraneContainerSession, prepare_container
from uitest.errors import ElementNotFoundError, InfrastructureError, UnexpectedAlertError
from uitest.expectations import ElementEnabled, NoAlert, ScreenIs, ScreenSpec
from uitest.model import Alert, Rect
from uitest.policy import AlertPolicy, AlertRule, AuthorizedTarget, TargetRegistry

BUNDLE = 'com.example.demo'
BUDGET = Budget(timeout=3.0, poll_interval=0.2, settle_for=0.2, settle_timeout=3.0,
                infra_retries=2, infra_backoff=0.5, infra_backoff_max=1.0)

NOTIFICATION_PROMPT = Alert(
    identifier='', title='"Demo" Would Like to Send You Notifications',
    message='', buttons=("Don't Allow", 'Allow'), system=True,
)
POLICY = AlertPolicy((
    AlertRule(name='ios_notifications', system=True, dismiss_with="Don't Allow",
              title='"Demo" Would Like to Send You Notifications'),
))

LOGIN = ScreenSpec('screen.login', name='Login',
                   required=(Selector.by_id('login.user'), Selector.by_id('login.submit')),
                   forbidden=(Selector.by_id('landing.title'),))
LANDING = ScreenSpec('screen.landing', name='Landing',
                     required=(Selector.by_id('landing.title'),))


def demo_app(*, prompt: bool = False) -> FakeApp:
    """A two-screen demo app: sign in, land on a welcome screen."""
    login = FakeScreen('screen.login', [
        FakeElement('login.user', type='TextField', placeholder='user', value='',
                    frame=Rect(20, 120, 350, 44)),
        FakeElement('login.password', type='SecureTextField', protected=True, value=None,
                    frame=Rect(20, 180, 350, 44)),
        # Low on the screen, so the keyboard raised by typing covers it.
        FakeElement('login.submit', type='Button', label='Sign in', enabled=False,
                    frame=Rect(20, 520, 350, 44)),
    ], alert=NOTIFICATION_PROMPT if prompt else None)
    landing = FakeScreen('screen.landing', [
        FakeElement('landing.title', type='StaticText', label='Welcome', frame=Rect(20, 80, 350, 30)),
        FakeElement('landing.balance', type='StaticText', label='0', frame=Rect(20, 130, 350, 30)),
    ])
    app = FakeApp(BUNDLE, [login, landing], container_id='profile-qa-1')

    def recompute(application, _element, _text=None):
        screen = application.screens['screen.login']
        user = screen.find('login.user')
        typed_password = any(ref == 'login.password' for ref, _ in application.typed)
        screen.find('login.submit').enabled = bool(user.value) and typed_password
        screen.keyboard = True

    app.on_type('screen.login', 'login.user', recompute)
    app.on_type('screen.login', 'login.password', recompute)

    def submit(application, _element):
        application.goto('screen.landing', animate_reads=3)  # a real push transition

    app.on_tap('screen.login', 'login.submit', submit)
    return app


class DemoFlowTests(unittest.TestCase):
    def tearDown(self):
        crane._HELD.clear()

    def harness(self, app, *, read_faults=0):
        manager = FakeContainerManager(BUNDLE, ('DEFAULT', 'profile-qa-1', 'profile-qa-2'),
                                       active='DEFAULT')
        target = AuthorizedTarget(bundle_id=BUNDLE, environment='demo',
                                  authorization_ref='QA-1042', owner='qa@example.com')
        prepare_container(manager, BUNDLE, 'profile-qa-1')
        session = CraneContainerSession(manager, BUNDLE, 'profile-qa-1')
        session.open()
        clock = FakeClock()
        backend = MockBackend(app, clock=clock, read_faults=read_faults)
        adapter = UITestAdapter(backend, target=target, budget=BUDGET, alert_policy=POLICY,
                               container=session, clock=clock, sleep=clock.sleep)
        return adapter, manager, clock

    def test_full_sign_in_journey(self):
        app = demo_app()
        adapter, manager, clock = self.harness(app)
        with adapter:
            adapter.verify_screen(LOGIN)
            adapter.type_text(Selector.by_id('login.user'), 'qa-tester')
            adapter.type_text(Selector.by_id('login.password'), 'demo-pass', secret=True,
                              expect=ElementEnabled(Selector.by_id('login.submit'), True))
            adapter.tap_element(Selector.by_id('login.submit'), expect=ScreenIs('screen.landing'))
            landing = adapter.verify_screen(LANDING)

        self.assertEqual(landing.screen_id, 'screen.landing')
        self.assertEqual([record.verb for record in adapter.history], ['type', 'type', 'tap'])
        self.assertTrue(all(not record.fallback_selector for record in adapter.history),
                        'every step addressed an accessibility identifier')
        # The keyboard covered the submit button, so one bounded recovery ran.
        self.assertEqual(app.keyboard_dismissals, 1)
        self.assertEqual(adapter.history[-1].recovered, 'keyboard_obstruction')
        self.assertEqual(app.taps, [('screen.login', 'login.submit')])
        # Cleanup: container released and detached, no switch during the test.
        self.assertEqual(manager.switches, [('profile-qa-1', False)])
        self.assertEqual(manager.detached, 1)
        self.assertTrue(app.closed)
        self.assertNotIn(BUNDLE, crane._HELD)
        self.assertGreater(clock.total_slept, 0.0, 'waits were time-bounded, not fixed sleeps')

    def test_declared_system_prompt_is_declined_then_the_flow_continues(self):
        app = demo_app(prompt=True)
        adapter, _manager, _clock = self.harness(app)
        with adapter:
            adapter.dismiss_alert(expect=NoAlert())
            adapter.verify_screen(LOGIN)
        self.assertEqual(app.alert_taps, ["Don't Allow"])

    def test_an_undeclared_prompt_halts_the_journey_untouched(self):
        app = demo_app()
        app.screens['screen.login'].alert = Alert(
            identifier='', title='"Demo" Would Like to Access Your Contacts',
            buttons=("Don't Allow", 'OK'), system=True)
        adapter, _manager, _clock = self.harness(app)
        with adapter:
            with self.assertRaises(UnexpectedAlertError):
                adapter.type_text(Selector.by_id('login.user'), 'qa-tester')
        self.assertEqual(app.alert_taps, [], 'no button was pressed on an undeclared prompt')
        self.assertEqual(app.typed, [])

    def test_a_transport_fault_is_absorbed_by_backoff(self):
        app = demo_app()
        adapter, _manager, clock = self.harness(app, read_faults=2)
        with adapter:
            adapter.verify_screen(LOGIN)
        self.assertGreaterEqual(clock.total_slept, 1.5)

    def test_registry_gates_the_target(self):
        path = Path(__file__).resolve().parents[1] / 'uitest' / 'authorized_targets.example.json'
        registry = TargetRegistry.from_file(path)
        target = registry.require('com.example.demo')
        self.assertEqual(target.environment, 'demo')
        self.assertFalse(target.allow_business_writes)
        from uitest.errors import TargetNotAuthorizedError
        with self.assertRaises(TargetNotAuthorizedError):
            registry.require('jp.coinplus.app')


# --- WebDriverAgent backend -------------------------------------------------

SOURCE_TREE = {
    'type': 'XCUIElementTypeApplication', 'name': 'Demo', 'label': 'Demo',
    'rect': {'x': 0, 'y': 0, 'width': 390, 'height': 844}, 'enabled': True, 'visible': True,
    'children': [
        {'type': 'XCUIElementTypeNavigationBar', 'name': 'screen.login',
         'rect': {'x': 0, 'y': 44, 'width': 390, 'height': 44}, 'children': []},
        {'type': 'XCUIElementTypeTextField', 'name': 'login.user', 'label': 'User',
         'value': 'qa-tester', 'placeholderValue': 'user', 'enabled': True, 'visible': True,
         'hittable': True, 'hasFocus': True,
         'rect': {'x': 20, 'y': 120, 'width': 350, 'height': 44}, 'children': []},
        {'type': 'XCUIElementTypeSecureTextField', 'name': 'login.password', 'value': None,
         'enabled': True, 'visible': True, 'hittable': True,
         'rect': {'x': 20, 'y': 180, 'width': 350, 'height': 44}, 'children': []},
        {'type': 'XCUIElementTypeButton', 'name': 'login.submit', 'label': 'Sign in',
         'enabled': False, 'visible': True, 'hittable': False,
         'rect': {'x': 20, 'y': 250, 'width': 350, 'height': 44}, 'children': []},
        {'type': 'XCUIElementTypeKeyboard', 'name': '', 'visible': True,
         'rect': {'x': 0, 'y': 500, 'width': 390, 'height': 344}, 'children': []},
    ],
}

ALERT_TREE = {
    'type': 'XCUIElementTypeApplication', 'name': 'Demo',
    'rect': {'x': 0, 'y': 0, 'width': 390, 'height': 844},
    'children': [
        {'type': 'XCUIElementTypeAlert', 'name': 'alert.terms', 'label': 'Terms',
         'rect': {'x': 20, 'y': 300, 'width': 350, 'height': 200},
         'children': [
             {'type': 'XCUIElementTypeStaticText', 'label': 'Terms', 'children': []},
             {'type': 'XCUIElementTypeStaticText', 'label': 'Accept later?', 'children': []},
             {'type': 'XCUIElementTypeButton', 'label': 'Later', 'children': []},
             {'type': 'XCUIElementTypeButton', 'label': 'Accept', 'children': []},
         ]},
    ],
}


class FakeResponse:
    def __init__(self, payload, status=200, text=''):
        self._payload = payload
        self.status_code = status
        self._text = text

    def json(self):
        if self._payload is None:
            raise ValueError('not json')
        return self._payload


class FakeHttpSession:
    """Records WDA requests and replays scripted responses."""

    def __init__(self, source=None, elements=None):
        self.source = source if source is not None else SOURCE_TREE
        self.elements = elements
        self.calls = []
        self.failures = {}

    def fail(self, path_fragment, response):
        self.failures[path_fragment] = response

    def _respond(self, method, url, payload=None):
        self.calls.append((method, url, payload))
        for fragment, response in self.failures.items():
            if fragment in url:
                return response
        if url.endswith('/source?format=json'):
            return FakeResponse({'value': self.source, 'sessionId': 's1'})
        if url.endswith('/wda/activeAppInfo'):
            return FakeResponse({'value': {'bundleId': BUNDLE}, 'sessionId': 's1'})
        if url.endswith('/elements'):
            found = self.elements if self.elements is not None else [{'ELEMENT': 'E1'}]
            return FakeResponse({'value': found, 'sessionId': 's1'})
        return FakeResponse({'value': None, 'sessionId': 's1'})

    def get(self, url, timeout=None):
        return self._respond('GET', url)

    def post(self, url, json=None, timeout=None):
        return self._respond('POST', url, json)

    def delete(self, url, timeout=None):
        return self._respond('DELETE', url)


class WebDriverAgentMappingTests(unittest.TestCase):
    def backend(self, session=None):
        session = session or FakeHttpSession()
        return WebDriverAgentBackend(session, 'http://127.0.0.1:8100', 's1', BUNDLE), session

    def test_maps_the_xcuitest_tree_including_keyboard_and_secure_field(self):
        backend, _session = self.backend()
        snapshot = backend.capture()
        self.assertEqual(snapshot.bundle_id, BUNDLE)
        self.assertEqual(snapshot.screen_id, 'screen.login')
        self.assertEqual(snapshot.window, Rect(0, 0, 390, 844))
        self.assertEqual(snapshot.keyboard, Rect(0, 500, 390, 344))
        by_id = {element.identifier: element for element in snapshot.elements()}
        self.assertEqual(by_id['login.user'].value, 'qa-tester')
        self.assertEqual(by_id['login.user'].placeholder, 'user')
        self.assertTrue(by_id['login.user'].focused)
        self.assertTrue(by_id['login.password'].protected)
        self.assertIsNone(by_id['login.password'].value)
        self.assertFalse(by_id['login.submit'].enabled)
        self.assertFalse(by_id['login.submit'].hittable)

    def test_maps_an_alert_with_its_buttons(self):
        backend, _session = self.backend(FakeHttpSession(source=ALERT_TREE))
        snapshot = backend.capture()
        self.assertIsNotNone(snapshot.alert)
        self.assertEqual(snapshot.alert.identifier, 'alert.terms')
        self.assertEqual(snapshot.alert.message, 'Accept later?')
        self.assertEqual(snapshot.alert.buttons, ('Later', 'Accept'))

    def test_tap_resolves_the_identifier_then_clicks_that_element(self):
        backend, session = self.backend()
        element = next(item for item in backend.capture().elements()
                       if item.identifier == 'login.submit')
        backend.tap(element.ref)
        posts = [url for method, url, _ in session.calls if method == 'POST']
        self.assertTrue(any(url.endswith('/elements') for url in posts))
        self.assertTrue(any(url.endswith('/element/E1/click') for url in posts))
        find = next(payload for method, url, payload in session.calls
                    if url.endswith('/elements'))
        self.assertEqual(find, {'using': 'accessibility id', 'value': 'login.submit'})

    def test_type_text_focuses_then_types_through_the_keyboard(self):
        backend, session = self.backend()
        element = next(item for item in backend.capture().elements()
                       if item.identifier == 'login.user')
        backend.type_text(element.ref, 'abc')
        value_call = next(payload for _m, url, payload in session.calls
                          if url.endswith('/element/E1/value'))
        self.assertEqual(value_call, {'value': ['a', 'b', 'c']})
        self.assertTrue(any(url.endswith('/element/E1/click') for _m, url, _p in session.calls),
                        'the element is focused by a real tap before typing')

    def test_an_element_without_an_identifier_cannot_be_acted_on(self):
        backend, _session = self.backend()
        keyboard = next(item for item in backend.capture().elements()
                        if item.type == 'XCUIElementTypeKeyboard')
        with self.assertRaises(ElementNotFoundError) as caught:
            backend.tap(keyboard.ref)
        self.assertIn('no accessibility identifier', str(caught.exception))

    def test_a_duplicate_identifier_is_refused_rather_than_guessed(self):
        session = FakeHttpSession(elements=[{'ELEMENT': 'E1'}, {'ELEMENT': 'E2'}])
        backend, _session = self.backend(session)
        element = next(item for item in backend.capture().elements()
                       if item.identifier == 'login.submit')
        with self.assertRaises(ElementNotFoundError) as caught:
            backend.tap(element.ref)
        self.assertIn('identifiers must be unique', str(caught.exception))

    def test_server_and_session_faults_become_infrastructure_errors(self):
        session = FakeHttpSession()
        session.fail('/source', FakeResponse({'value': {}}, status=502))
        backend, _session = self.backend(session)
        with self.assertRaises(InfrastructureError):
            backend.capture()

        dead = FakeHttpSession()
        dead.fail('/source', FakeResponse({'value': {'error': 'invalid session id'}}, status=404))
        backend, _session = self.backend(dead)
        with self.assertRaises(InfrastructureError) as caught:
            backend.capture()
        self.assertIn('session fault', str(caught.exception))

    def test_a_connection_error_is_an_infrastructure_error(self):
        class Dead(FakeHttpSession):
            def get(self, url, timeout=None):
                raise OSError('connection refused')

        backend, _session = self.backend(Dead())
        with self.assertRaises(InfrastructureError) as caught:
            backend.capture()
        self.assertIn('connection refused', str(caught.exception))

    def test_snapshot_carries_the_container_id_from_the_provider(self):
        """Audit finding C2: a dump must prove which container produced it."""
        session = FakeHttpSession()
        calls = {'n': 0}

        def provider():
            calls['n'] += 1
            return 'profile-qa-1'

        backend = WebDriverAgentBackend(session, 'http://127.0.0.1:8100', 's1', BUNDLE,
                                        container_id_provider=provider)
        first = backend.capture()
        second = backend.capture()
        self.assertEqual(first.container_id, 'profile-qa-1')
        self.assertEqual(second.container_id, 'profile-qa-1')
        self.assertEqual(calls['n'], 1, 'the container cannot change mid-session, so read it once')

    def test_a_failing_container_provider_leaves_the_stamp_empty(self):
        """No evidence is acceptable; wrong evidence is not."""
        def provider():
            raise RuntimeError('crane rpc down')

        backend = WebDriverAgentBackend(FakeHttpSession(), 'http://127.0.0.1:8100', 's1',
                                        BUNDLE, container_id_provider=provider)
        self.assertEqual(backend.capture().container_id, '')

    def test_crane_rpc_manager_supplies_the_stamp(self):
        from uitest.crane_rpc import CraneRpcManager
        from tests.test_uitest_crane_rpc import FakeHostSession

        host = FakeHostSession(active='profile-qa-1')
        manager = CraneRpcManager(session_factory=lambda *_a: host)
        backend = WebDriverAgentBackend(
            FakeHttpSession(), 'http://127.0.0.1:8100', 's1', BUNDLE,
            container_id_provider=lambda: manager.active_container_id(BUNDLE))
        self.assertEqual(backend.capture().container_id, 'profile-qa-1')
        manager.detach()

    def test_close_deletes_the_session_once(self):
        backend, session = self.backend()
        backend.close()
        backend.close()
        deletes = [url for method, url, _ in session.calls if method == 'DELETE']
        self.assertEqual(deletes, ['http://127.0.0.1:8100/session/s1'])

    def test_adapter_runs_against_the_wda_backend_double(self):
        """The adapter is backend-agnostic: same guards, WDA transport."""
        session = FakeHttpSession()
        backend, _ = self.backend(session)
        target = AuthorizedTarget(bundle_id=BUNDLE, environment='demo',
                                  authorization_ref='QA-1042', owner='qa@example.com')
        clock = FakeClock()
        adapter = UITestAdapter(backend, target=target, budget=BUDGET, clock=clock,
                                sleep=clock.sleep)
        snapshot = adapter.verify_screen(LOGIN)
        self.assertEqual(snapshot.screen_id, 'screen.login')
        # login.submit is disabled and under the keyboard, so a tap must refuse.
        from uitest.errors import ElementNotInteractableError
        with self.assertRaises(ElementNotInteractableError):
            adapter.tap_element(Selector.by_id('login.submit'),
                                expect=ScreenIs('screen.landing'), timeout=0.5)
        self.assertFalse(any(url.endswith('/click') for _m, url, _p in session.calls))


if __name__ == '__main__':
    unittest.main()
