"""Crane container lifecycle: selection before the test, no switching inside it."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uitest.crane as crane
from uitest.backends.mock import FakeClock, FakeContainerManager
from uitest.crane import ContainerState, CraneContainerSession, prepare_container
from uitest.errors import ContainerChangedError, ContainerNotReadyError, SessionClosedError

APP = 'com.example.demo'


class ContainerStateTests(unittest.TestCase):
    def test_rejects_a_payload_crane_did_not_confirm(self):
        for payload in ({'ok': False, 'error': 'CraneManager unavailable'}, 'boom', None, {}):
            with self.subTest(payload=payload):
                with self.assertRaises(ContainerNotReadyError):
                    ContainerState.from_payload(payload)

    def test_parses_a_confirmed_payload(self):
        state = ContainerState.from_payload({
            'ok': True, 'app_id': APP, 'supported': True,
            'active_container_id': 'c2', 'containers': ['DEFAULT', 'c1', 'c2'],
        })
        self.assertEqual(state.active_container_id, 'c2')
        self.assertEqual(state.containers, ('DEFAULT', 'c1', 'c2'))
        self.assertIn('active=', state.describe())


class PrepareContainerTests(unittest.TestCase):
    def tearDown(self):
        crane._HELD.clear()

    def test_switches_then_confirms(self):
        manager = FakeContainerManager(APP, ('DEFAULT', 'c1', 'c2'), active='c1')
        state = prepare_container(manager, APP, 'c2', reload=True)
        self.assertEqual(manager.switches, [('c2', True)])
        self.assertEqual(state.active_container_id, 'c2')

    def test_no_switch_when_already_active(self):
        manager = FakeContainerManager(APP, ('c1',), active='c1')
        prepare_container(manager, APP, 'c1')
        self.assertEqual(manager.switches, [])

    def test_refuses_unknown_container_and_unmanaged_app(self):
        manager = FakeContainerManager(APP, ('c1',), active='c1')
        with self.assertRaises(ContainerNotReadyError):
            prepare_container(manager, APP, 'nope')
        with self.assertRaises(ContainerNotReadyError):
            prepare_container(FakeContainerManager(APP, ('c1',), 'c1', supported=False), APP, 'c1')
        with self.assertRaises(ContainerNotReadyError):
            prepare_container(manager, APP, '')
        self.assertEqual(manager.switches, [])

    def test_refuses_to_switch_while_a_session_holds_the_app(self):
        manager = FakeContainerManager(APP, ('c1', 'c2'), active='c1')
        with CraneContainerSession(manager, APP, 'c1'):
            with self.assertRaises(ContainerNotReadyError) as caught:
                prepare_container(manager, APP, 'c2')
        self.assertIn('already holds', str(caught.exception))
        self.assertEqual(manager.switches, [], 'no switch happened while the test held the app')


class ContainerSessionTests(unittest.TestCase):
    def tearDown(self):
        crane._HELD.clear()

    def test_open_verifies_state_and_never_switches(self):
        manager = FakeContainerManager(APP, ('DEFAULT', 'c1'), active='c1')
        with CraneContainerSession(manager, APP, 'c1') as session:
            self.assertEqual(session.state.active_container_id, 'c1')
            session.assert_stable()
        self.assertEqual(manager.switches, [])
        self.assertEqual(manager.detached, 1)

    def test_open_fails_when_the_expected_container_is_not_active(self):
        manager = FakeContainerManager(APP, ('c1', 'c2'), active='c2')
        with self.assertRaises(ContainerNotReadyError) as caught:
            CraneContainerSession(manager, APP, 'c1').open()
        self.assertIn('does not switch containers', str(caught.exception))

    def test_open_fails_when_crane_is_unavailable(self):
        manager = FakeContainerManager(APP, ('c1',), active='c1', ok=False)
        with self.assertRaises(ContainerNotReadyError):
            CraneContainerSession(manager, APP, 'c1').open()

    def test_open_requires_a_selected_container(self):
        manager = FakeContainerManager(APP, ('c1',), active='c1')
        with self.assertRaises(ContainerNotReadyError) as caught:
            CraneContainerSession(manager, APP, '').open()
        self.assertIn('No container was selected', str(caught.exception))

    def test_assert_stable_detects_a_mid_test_switch(self):
        manager = FakeContainerManager(APP, ('c1', 'c2'), active='c1')
        session = CraneContainerSession(manager, APP, 'c1')
        session.open()
        session.assert_stable()
        manager.active = 'c2'  # another worker switched underneath us
        with self.assertRaises(ContainerChangedError) as caught:
            session.assert_stable()
        self.assertIn('changed mid-test', str(caught.exception))
        session.close()

    def test_recheck_interval_throttles_reads_but_still_catches_change(self):
        manager = FakeContainerManager(APP, ('c1', 'c2'), active='c1')
        clock = FakeClock()
        session = CraneContainerSession(manager, APP, 'c1', recheck_interval=5.0, clock=clock)
        session.open()
        reads = manager.info_calls
        session.assert_stable()
        self.assertEqual(manager.info_calls, reads, 'cached within the interval')
        clock.sleep(6.0)
        manager.active = 'c2'
        with self.assertRaises(ContainerChangedError):
            session.assert_stable()
        session.close()

    def test_close_is_idempotent_and_releases_the_hold(self):
        manager = FakeContainerManager(APP, ('c1',), active='c1')
        session = CraneContainerSession(manager, APP, 'c1')
        session.open()
        session.close()
        session.close()
        self.assertEqual(manager.detached, 1)
        self.assertNotIn(APP, crane._HELD)
        with self.assertRaises(SessionClosedError):
            session.assert_stable()
        # The hold is released, so the next run may select a different container.
        prepare_container(manager, APP, 'c1')

    def test_close_releases_the_hold_even_if_detach_fails(self):
        class BrokenManager(FakeContainerManager):
            def detach(self):
                raise RuntimeError('rpc already gone')

        manager = BrokenManager(APP, ('c1',), active='c1')
        session = CraneContainerSession(manager, APP, 'c1')
        session.open()
        session.close()
        self.assertNotIn(APP, crane._HELD)

    def test_two_sessions_cannot_hold_the_same_app(self):
        manager = FakeContainerManager(APP, ('c1',), active='c1')
        first = CraneContainerSession(manager, APP, 'c1')
        first.open()
        with self.assertRaises(ContainerNotReadyError):
            CraneContainerSession(manager, APP, 'c1').open()
        first.close()

    def test_module_exposes_no_identity_or_keychain_surface(self):
        """The container module selects containers; it must not reach for device identity.

        Scans executable code only -- comments and docstrings are stripped, so
        the prose that *documents* the boundary does not trip the check.
        """
        import io
        import tokenize

        path = Path(__file__).resolve().parents[1] / 'uitest' / 'crane.py'
        code_tokens = [
            token.string.lower()
            for token in tokenize.generate_tokens(io.StringIO(path.read_text(encoding='utf-8')).readline)
            if token.type not in (tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE)
        ]
        code = ' '.join(code_tokens)
        for forbidden in ('keychain', 'udid', 'idfa', 'identifierforvendor',
                          'serialnumber', 'serial_number', 'advertising'):
            self.assertNotIn(forbidden, code,
                             f'{forbidden} must not appear in executable code of the container module')


if __name__ == '__main__':
    unittest.main()
