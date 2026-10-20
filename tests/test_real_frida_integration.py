"""Tests that only mean something with the real `frida` package, or a device.

These exist to make the third result category visible. Under the stub they
**skip**, so the suite output distinguishes:

* MOCK PASS            - verified against mocks
* REAL INTEGRATION PASS - verified against the real package / a device
* NOT TESTED           - skipped here, still needs a licensed test environment

Scope limits, deliberately: nothing here registers an account, signs in, claims
a reward or moves money. They read the local Frida installation and, at most,
enumerate attached devices. Driving an app belongs in a licensed test
environment with an app you own -- see `uitest/` and
`docs/ui-automation-architecture.md`.

Run an integration pass with::

    AIRWALLET_REQUIRE_REAL_FRIDA=1 python -m unittest discover -s tests

With that variable set, the stub refuses to install, so the run cannot quietly
degrade into a mock run.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from frida_stub import install_frida_stub, requires_real_frida, using_stub

install_frida_stub()  # no-op when the real frida is installed

import driver


def usb_devices():
    """Attached USB devices, or [] when frida cannot be used."""
    if using_stub():
        return []
    import frida

    try:
        return [device for device in frida.enumerate_devices() if device.type == 'usb']
    except Exception:
        return []


class RealFridaPackageTests(unittest.TestCase):
    """Need the real frida / frida-tools installed. No device required."""

    @requires_real_frida('reads the ObjC bridge shipped with frida-tools')
    def test_agent_source_loads_the_real_objc_bridge(self):
        source = driver.load_agent_source()
        self.assertIn('const ObjC = bridge;', source)
        self.assertIn('rpc.exports', source, 'agent.js must be appended to the bridge')
        self.assertGreater(len(source), 10_000, 'the ObjC bridge should be substantial')

    @requires_real_frida('calls frida.enumerate_devices()')
    def test_enumerate_devices_returns_a_list(self):
        import frida

        devices = frida.enumerate_devices()
        self.assertIsInstance(devices, list)
        for device in devices:
            self.assertTrue(hasattr(device, 'id'))
            self.assertTrue(hasattr(device, 'type'))

    @requires_real_frida('inspects the installed frida version')
    def test_installed_frida_is_the_pinned_major_version(self):
        import frida

        requirement = (ROOT / 'requirements.txt').read_text(encoding='utf-8')
        pinned = next((line.split('==')[1].strip() for line in requirement.splitlines()
                       if line.strip().startswith('frida==')), '')
        self.assertTrue(pinned, 'requirements.txt should pin frida')
        self.assertEqual(frida.__version__.split('.')[0], pinned.split('.')[0],
                         f'installed frida {frida.__version__} vs pinned {pinned}')


class AttachedDeviceTests(unittest.TestCase):
    """Need a real iPhone attached with frida-server running.

    Read-only: enumerates devices and installed applications. Does not spawn,
    attach to, or drive any app.
    """

    @classmethod
    def setUpClass(cls):
        if using_stub():
            raise unittest.SkipTest('needs the real frida package')
        if not usb_devices():
            raise unittest.SkipTest('no USB device attached')

    def test_a_usb_device_is_reachable(self):
        devices = usb_devices()
        self.assertTrue(devices)
        for device in devices:
            self.assertTrue(str(device.id))

    def test_device_lists_applications(self):
        """frida-server responds. Read-only; nothing is launched."""
        device = usb_devices()[0]
        applications = device.enumerate_applications()
        self.assertIsInstance(applications, list)


if __name__ == '__main__':
    unittest.main()
