"""The frida test stub must never change behaviour on a real workstation.

If the stub shadowed a real `frida`, the suite would stop testing the thing it
is meant to test. If it pretended to be a working device, it could make a test
pass that would fail against real Frida. Both are checked here.
"""
import re
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frida_stub
from frida_stub import STUB_MARKER, install_frida_stub, real_frida_available, using_stub


class StubIsolation(unittest.TestCase):
    """Each test gets a clean sys.modules view of frida/frida_tools."""

    def setUp(self):
        self._saved = {name: sys.modules.get(name) for name in ('frida', 'frida_tools')}

    def tearDown(self):
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def clear(self):
        for name in ('frida', 'frida_tools'):
            sys.modules.pop(name, None)


class NoOpWithRealFridaTests(StubIsolation):
    def test_does_nothing_when_a_real_frida_is_present(self):
        real = types.ModuleType('frida')          # no stub marker == "real"
        sys.modules['frida'] = real
        self.assertTrue(real_frida_available())
        self.assertFalse(install_frida_stub(), 'must not install over a real frida')
        self.assertIs(sys.modules['frida'], real)
        self.assertFalse(using_stub())

    def test_does_not_create_frida_tools_when_frida_is_real(self):
        sys.modules['frida'] = types.ModuleType('frida')
        sys.modules.pop('frida_tools', None)
        install_frida_stub()
        self.assertIsNone(sys.modules.get('frida_tools'),
                          'a real workstation supplies its own frida_tools')


class InstallWhenMissingTests(StubIsolation):
    def test_installs_both_modules_when_frida_cannot_be_imported(self):
        self.clear()
        with patch.object(frida_stub, 'real_frida_available',
                                        return_value=False):
            self.assertTrue(install_frida_stub())
        self.assertTrue(getattr(sys.modules['frida'], STUB_MARKER, False))
        self.assertTrue(getattr(sys.modules['frida_tools'], STUB_MARKER, False))
        self.assertTrue(using_stub())

    def test_is_idempotent(self):
        self.clear()
        with patch.object(frida_stub, 'real_frida_available',
                                        return_value=False):
            install_frida_stub()
            first = sys.modules['frida']
            install_frida_stub()
        self.assertIs(sys.modules['frida'], first, 'must not rebuild the stub')


class StubIsInertTests(StubIsolation):
    def setUp(self):
        super().setUp()
        self.clear()
        self._patch = patch.object(frida_stub, 'real_frida_available',
                                                 return_value=False)
        self._patch.start()
        install_frida_stub()
        self.frida = sys.modules['frida']

    def tearDown(self):
        self._patch.stop()
        super().tearDown()

    def test_enumerates_no_devices(self):
        self.assertEqual(self.frida.enumerate_devices(), [])

    def test_every_device_entry_point_fails_loudly(self):
        for name in ('get_device', 'get_usb_device', 'get_local_device',
                     'get_remote_device', 'attach', 'spawn', 'kill'):
            with self.subTest(call=name):
                with self.assertRaises(RuntimeError) as caught:
                    getattr(self.frida, name)('anything')
                self.assertIn('frida is not installed', str(caught.exception))
                self.assertIn('tests/frida_stub.py', str(caught.exception))

    def test_error_types_the_project_references_exist(self):
        # Grepped from the project: frida.<Name> used in except clauses.
        for name in ('InvalidOperationError', 'ProcessNotFoundError',
                     'RPCException', 'TransportError'):
            with self.subTest(error=name):
                error = getattr(self.frida, name)
                self.assertTrue(issubclass(error, Exception))
                with self.assertRaises(error):
                    raise error('boom')

    def test_frida_tools_path_does_not_resolve_to_a_fake_agent(self):
        """driver.load_agent_source() must fail loudly, not load a stub bridge."""
        path = Path(sys.modules['frida_tools'].__file__)
        self.assertFalse(path.exists())

        import driver
        with self.assertRaises(OSError):
            driver.load_agent_source()


#: Project modules that pull frida in transitively, in either import form, so
#: the bootstrap must precede them.
PROJECT_IMPORT = re.compile(
    r'^(?:from|import)\s+'
    r'(driver|main|flow|inputs|diagnostics|airwallet_\w+|crane_container_manager'
    r'|powerselector|proxy_switcher|spawn_throttle|respring_all|src\.|scripts\.)',
    re.MULTILINE,
)

FRIDA_DEPENDENT = (
    'test_wrapper.py', 'test_launch_order.py', 'test_device_assignment.py',
    'test_proxy.py', 'test_respring.py', 'test_credential_hygiene.py',
    'test_alert_guard_diagnostics.py', 'test_resource_cleanup.py',
)


class WiringTests(unittest.TestCase):
    def test_every_frida_dependent_test_module_installs_the_stub(self):
        """A module that forgets the bootstrap silently drops out of the suite."""
        tests_dir = Path(__file__).resolve().parent
        for name in FRIDA_DEPENDENT:
            source = (tests_dir / name).read_text(encoding='utf-8')
            with self.subTest(module=name):
                if 'install_frida_stub()' not in source:
                    self.fail(f'{name} does not install the frida stub')
                bootstrap = source.index('install_frida_stub()')
                match = PROJECT_IMPORT.search(source)
                if match is None:
                    self.fail(f'{name} imports no known project module; update PROJECT_IMPORT')
                if bootstrap > match.start():
                    self.fail(f'{name}: install_frida_stub() must run before the project '
                              'imports that pull in frida')

    def test_no_module_keeps_its_own_inline_stub(self):
        """One shared stub, so its guarantees hold everywhere."""
        tests_dir = Path(__file__).resolve().parent
        for path in sorted(tests_dir.glob('test_*.py')):
            if path.name == Path(__file__).name:
                continue   # this module builds fake frida modules on purpose
            source = path.read_text(encoding='utf-8')
            with self.subTest(module=path.name):
                if "ModuleType('frida')" in source or 'ModuleType("frida")' in source:
                    self.fail(f'{path.name} builds its own frida stub; '
                              'use the shared tests/frida_stub.py')


if __name__ == '__main__':
    unittest.main()
