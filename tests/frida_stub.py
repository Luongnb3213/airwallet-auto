"""Let the test suite run on a machine without `frida` installed.

`frida` is a native package that only matters on a workstation driving a real
iPhone, but `driver.py`, `scripts/proxy_switcher.py`, `scripts/powerselector.py`
and others import it at module scope. Without it, five test modules could not
even be imported -- including the two that cover `airwallet_dom_runner` and
`airwallet_full_flow` -- so the suite silently shrank to the modules that happen
not to touch Frida.

:func:`install_frida_stub` registers inert `frida` and `frida_tools` modules in
``sys.modules`` when the real ones are missing. Call it **before** importing any
project module.

Two deliberate properties:

* **No-op when the real frida is installed.** On a workstation the tests run
  against the real package; the stub never shadows it.
* **Inert and loud.** The stub enumerates no devices and raises a clear error if
  anything tries to actually spawn, attach or read the agent bridge. It does not
  pretend to be a working device, so it cannot make a test pass that would fail
  against real Frida -- tests that need device behaviour supply their own mocks,
  which is what all of them already do.

Usage, at the top of a test module and before the project imports::

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from frida_stub import install_frida_stub

    install_frida_stub()
"""
from __future__ import annotations

import os
import sys
import types

#: Exception types the project references as `frida.<Name>`.
_ERROR_NAMES = (
    "InvalidOperationError",
    "ProcessNotFoundError",
    "RPCException",
    "TransportError",
    "ServerNotRunningError",
    "ProcessNotRespondingError",
    "PermissionDeniedError",
    "NotSupportedError",
)

STUB_MARKER = "__airwallet_test_stub__"

#: Set this to demand the real package; the stub then refuses to install.
REQUIRE_REAL_ENV = "AIRWALLET_REQUIRE_REAL_FRIDA"


class RealFridaRequired(RuntimeError):
    """Raised when an integration run would silently fall back to mocks."""


def real_frida_available() -> bool:
    """Whether the genuine `frida` package can be imported."""
    existing = sys.modules.get("frida")
    if existing is not None:
        return not getattr(existing, STUB_MARKER, False)
    try:
        import frida  # noqa: F401
    except Exception:
        return False
    return True


def _unavailable(action: str):
    def fail(*_args, **_kwargs):
        raise RuntimeError(
            f"frida is not installed: cannot {action}. This is the test stub from "
            "tests/frida_stub.py. A test that needs device behaviour must patch it "
            "with its own mock."
        )

    return fail


def _build_frida() -> types.ModuleType:
    module = types.ModuleType("frida")
    setattr(module, STUB_MARKER, True)
    module.__doc__ = "Inert test stub (tests/frida_stub.py), not the real frida."
    module.__version__ = "0-stub"

    class FridaError(Exception):
        """Base of the stub's error hierarchy."""

    module.Error = FridaError
    for name in _ERROR_NAMES:
        setattr(module, name, type(name, (FridaError,), {}))

    # No devices: anything that wants one must mock it.
    module.enumerate_devices = lambda *_a, **_k: []
    module.get_device = _unavailable("get a device")
    module.get_usb_device = _unavailable("get a USB device")
    module.get_local_device = _unavailable("get the local device")
    module.get_remote_device = _unavailable("get a remote device")
    module.attach = _unavailable("attach to a process")
    module.spawn = _unavailable("spawn a process")
    module.kill = _unavailable("kill a process")
    return module


def _build_frida_tools() -> types.ModuleType:
    module = types.ModuleType("frida_tools")
    setattr(module, STUB_MARKER, True)
    module.__doc__ = "Inert test stub (tests/frida_stub.py), not the real frida-tools."
    # driver.load_agent_source() resolves the ObjC bridge relative to this path.
    # Point it somewhere that does not exist, so an unpatched real read fails
    # loudly instead of loading a fake agent. Every test that reaches
    # load_agent_source already patches it.
    module.__file__ = "<frida_tools stub: tests/frida_stub.py>"
    return module


def real_frida_required() -> bool:
    """Whether this run demands the real package.

    Set ``AIRWALLET_REQUIRE_REAL_FRIDA=1`` for an integration run on a
    workstation with a device attached. :func:`install_frida_stub` then refuses
    to substitute a stub, so an integration run cannot quietly degrade into a
    mock run and report success.
    """
    return str(os.environ.get(REQUIRE_REAL_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


def install_frida_stub() -> bool:
    """Register the stubs if `frida` is missing. Returns True if it did.

    Idempotent, and safe to call from several test modules.

    Raises :class:`RealFridaRequired` when ``AIRWALLET_REQUIRE_REAL_FRIDA`` is
    set but the real package is absent. Failing the run is the point: a silent
    fallback would turn "REAL INTEGRATION PASS" into "MOCK PASS" without anyone
    noticing.
    """
    if real_frida_available():
        return False
    if real_frida_required():
        raise RealFridaRequired(
            f"{REQUIRE_REAL_ENV} is set, but the real `frida` package cannot be imported. "
            "Install frida (and attach a device) for an integration run, or unset "
            f"{REQUIRE_REAL_ENV} to run against mocks."
        )
    if not getattr(sys.modules.get("frida"), STUB_MARKER, False):
        sys.modules["frida"] = _build_frida()
    if not getattr(sys.modules.get("frida_tools"), STUB_MARKER, False):
        sys.modules["frida_tools"] = _build_frida_tools()
    return True


def using_stub() -> bool:
    """Whether the frida currently in `sys.modules` is this stub."""
    return bool(getattr(sys.modules.get("frida"), STUB_MARKER, False))


def mode() -> str:
    """``'mock'`` or ``'real'`` -- which frida this process is testing against.

    Reported by the suite so a result is never ambiguous about what it proved.
    """
    return "mock" if using_stub() else "real"


def requires_real_frida(reason: str = ""):
    """Mark a test that only means something against a real device.

    Such a test is **skipped** under the stub rather than passing, because the
    stub cannot spawn, attach or read the ObjC bridge. A skip is honest
    ("NOT TESTED"); a pass would not be::

        @requires_real_frida('needs a device to attach to')
        def test_attaches_to_the_running_app(self):
            ...
    """
    import unittest

    note = f" ({reason})" if reason else ""
    return unittest.skipIf(using_stub(), f"needs the real frida package{note}")
