"""CraneRpcManager against a CraneHostSession double.

No device, no frida, no Crane. The session factory is injected, so these tests
cover the real error classification and session-reuse logic.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uitest.crane as crane
from uitest.crane import CraneContainerSession, ContainerState, prepare_container
from uitest.crane_rpc import CraneRpcManager
from uitest.errors import ContainerNotReadyError, InfrastructureError

APP = 'com.example.demo'


class FakeHostSession:
    """Stands in for crane_container_manager.CraneHostSession."""

    def __init__(self, *, active='c1', containers=('DEFAULT', 'c1', 'c2'),
                 supported=True, ok=True, raise_on=None, error=None):
        self.active = active
        self.containers = list(containers)
        self.supported = supported
        self.ok = ok
        self.raise_on = raise_on          # action name that should fail
        self.error = error or RuntimeError('boom')
        self.calls: list[tuple[str, str, dict]] = []
        self.exited = 0

    def call(self, action, app_id, **kwargs):
        self.calls.append((action, app_id, kwargs))
        if self.raise_on == action:
            raise self.error
        if action == 'info':
            return {'ok': self.ok, 'app_id': app_id, 'supported': self.supported,
                    'active_container_id': self.active,
                    'active_container_name': f'Profile {self.active}',
                    'containers': list(self.containers),
                    'frida_device_id': 'UDID-REDACTED'}
        if action == 'switch':
            self.active = kwargs.get('container_id', '')
            return {'ok': True, 'app_id': app_id, 'active_container_id': self.active}
        raise RuntimeError(f'Unsupported Crane action: {action}')

    def __exit__(self, exc_type, exc, tb):
        self.exited += 1
        return False


class ManagerTests(unittest.TestCase):
    def tearDown(self):
        crane._HELD.clear()

    def manager(self, session=None, **kwargs):
        session = session or FakeHostSession(**kwargs)
        opened = {'count': 0}

        def factory(device_id, host, spawn_slots):
            opened['count'] += 1
            session.opened_with = (device_id, host, spawn_slots)
            return session

        return CraneRpcManager(session_factory=factory), session, opened

    def test_info_returns_the_raw_crane_payload(self):
        manager, session, _ = self.manager()
        payload = manager.info(APP)
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['active_container_id'], 'c1')
        # The payload must be shaped for ContainerState without translation.
        state = ContainerState.from_payload(payload)
        self.assertEqual(state.containers, ('DEFAULT', 'c1', 'c2'))
        self.assertEqual(session.calls[0][0], 'info')

    def test_session_is_opened_once_and_reused(self):
        manager, _session, opened = self.manager()
        for _ in range(4):
            manager.info(APP)
        self.assertEqual(opened['count'], 1, 'assert_stable must not respawn the host per call')

    def test_session_opens_lazily(self):
        manager, _session, opened = self.manager()
        self.assertEqual(opened['count'], 0, 'constructing the manager must not spawn anything')
        manager.info(APP)
        self.assertEqual(opened['count'], 1)

    def test_switch_requires_an_explicit_container(self):
        manager, session, _ = self.manager()
        for empty in ('', '   ', None):
            with self.assertRaises(ContainerNotReadyError):
                manager.switch(APP, empty)
        self.assertEqual(session.calls, [], 'no RPC was issued')

    def test_switch_passes_the_reload_flag_through(self):
        manager, session, _ = self.manager()
        manager.switch(APP, 'c2', reload=True)
        action, app_id, kwargs = session.calls[-1]
        self.assertEqual((action, app_id), ('switch', APP))
        self.assertEqual(kwargs, {'container_id': 'c2', 'reload': True})

    def test_active_container_id_helper(self):
        manager, _session, _ = self.manager(active='c2')
        self.assertEqual(manager.active_container_id(APP), 'c2')

    def test_active_container_id_is_empty_when_crane_reports_nothing(self):
        manager, _session, _ = self.manager(active='')
        self.assertEqual(manager.active_container_id(APP), '')

    # --- error classification ----------------------------------------------

    def test_transport_fault_is_infrastructure(self):
        """Retryable: uitest.waits.retry_read backs off on this and nothing else."""
        manager, _session, _ = self.manager(
            raise_on='info', error=RuntimeError('early end-of-stream'))
        with self.assertRaises(InfrastructureError) as caught:
            manager.info(APP)
        self.assertIn('Crane RPC info failed', str(caught.exception))

    def test_crane_not_managing_the_app_is_a_state_error(self):
        """Not retryable: backing off will not install Crane."""
        manager, _session, _ = self.manager(
            raise_on='info',
            error=RuntimeError('Không có CraneManager trong CraneApplication host.'))
        with self.assertRaises(ContainerNotReadyError) as caught:
            manager.info(APP)
        self.assertIn('cannot manage this app', str(caught.exception))

    def test_a_failure_to_open_the_session_is_classified_too(self):
        def factory(*_args):
            raise RuntimeError('Current Python cannot import frida.')

        manager = CraneRpcManager(session_factory=factory)
        with self.assertRaises(InfrastructureError) as caught:
            manager.info(APP)
        self.assertIn('Crane RPC open failed', str(caught.exception))

    def test_ok_false_payload_is_passed_through_for_from_payload_to_reject(self):
        manager, _session, _ = self.manager(ok=False)
        payload = manager.info(APP)
        self.assertFalse(payload['ok'])
        with self.assertRaises(ContainerNotReadyError):
            ContainerState.from_payload(payload)

    # --- cleanup ------------------------------------------------------------

    def test_detach_closes_the_host_session_once(self):
        manager, session, _ = self.manager()
        manager.info(APP)
        manager.detach()
        manager.detach()
        self.assertEqual(session.exited, 1)

    def test_detach_without_a_session_is_a_no_op(self):
        manager, session, opened = self.manager()
        manager.detach()
        self.assertEqual(opened['count'], 0)
        self.assertEqual(session.exited, 0)

    def test_detach_survives_a_failing_teardown(self):
        class Broken(FakeHostSession):
            def __exit__(self, exc_type, exc, tb):
                raise RuntimeError('device already gone')

        manager, _session, _ = self.manager(session=Broken())
        manager.info(APP)
        manager.detach()   # must not raise

    def test_use_after_detach_is_refused(self):
        manager, _session, _ = self.manager()
        manager.info(APP)
        manager.detach()
        with self.assertRaises(InfrastructureError):
            manager.info(APP)

    def test_context_manager_detaches(self):
        manager, session, _ = self.manager()
        with manager as held:
            held.info(APP)
        self.assertEqual(session.exited, 1)

    # --- wired into the container session -----------------------------------

    def test_drives_prepare_container_and_a_container_session(self):
        manager, session, _ = self.manager(active='DEFAULT')
        prepare_container(manager, APP, 'c1')
        self.assertEqual(session.active, 'c1')
        with CraneContainerSession(manager, APP, 'c1') as bound:
            bound.assert_stable()
            self.assertEqual(bound.state.active_container_id, 'c1')
        self.assertEqual(session.exited, 1, 'closing the container session detached the RPC')

    def test_a_switch_underneath_the_session_is_caught(self):
        from uitest.errors import ContainerChangedError

        manager, session, _ = self.manager(active='c1')
        bound = CraneContainerSession(manager, APP, 'c1')
        bound.open()
        session.active = 'c2'
        with self.assertRaises(ContainerChangedError):
            bound.assert_stable()
        bound.close()

    def test_default_factory_is_not_invoked_during_import_or_construction(self):
        """frida must stay unimported until a call actually needs it."""
        manager = CraneRpcManager(device_id='auto')
        self.assertIsNone(manager._session)


if __name__ == '__main__':
    unittest.main()
