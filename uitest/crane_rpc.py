"""Bind :class:`~uitest.crane.ContainerManager` to the real Crane RPC.

Implements the three methods ``uitest.crane`` needs -- ``info``, ``switch``,
``detach`` -- on top of ``scripts/crane_container_manager.CraneHostSession``,
which spawns the Crane host app, attaches Frida to it and calls ``CraneManager``.

Two things this layer is responsible for.

**Session reuse.** ``CraneHostSession.__enter__`` spawns and attaches, and
``__exit__`` kills the host app. Doing that per call would make
:meth:`~uitest.crane.CraneContainerSession.assert_stable` -- which runs before
every mutating action -- cost a process spawn each time. So one session is
opened lazily and reused for the life of this manager; the Crane host app stays
running alongside the app under test until :meth:`detach`.

**Error classification.** ``uitest.waits.retry_read`` retries
:class:`~uitest.errors.InfrastructureError` with backoff and nothing else, so
the split has to be right:

* transport/spawn/attach faults, and a missing ``frida`` → ``InfrastructureError``
* Crane itself reporting it cannot manage the app → ``ContainerNotReadyError``

``frida`` is imported lazily inside the session factory, so this module can be
imported (and tested) on a machine without it.
"""
from __future__ import annotations

import logging
from pathlib import Path
import sys
from typing import Any, Callable

from .errors import ContainerNotReadyError, InfrastructureError

LOGGER = logging.getLogger("uitest.crane_rpc")

DEFAULT_HOST = "com.opa334.CraneApplication"

#: Crane says this when the host app has no CraneManager class -- a state
#: problem (Crane not installed/registered), not a transport fault.
_NOT_MANAGED_HINTS = ("không có cranemanager", "cranemanager unavailable",
                      "cranemanager not found")


def _default_session_factory(device_id: str, host: str, spawn_slots: int | None):
    """Open a real :class:`CraneHostSession`. Imports frida lazily."""
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from crane_container_manager import CraneHostSession  # noqa: PLC0415 - lazy by design

    session = CraneHostSession(device_id, host, keep_host=False, spawn_slots=spawn_slots)
    return session.__enter__()


def _classify(error: BaseException, action: str) -> BaseException:
    """Map a raw failure onto the adapter's error taxonomy."""
    message = str(error)
    lowered = message.lower()
    if any(hint in lowered for hint in _NOT_MANAGED_HINTS):
        return ContainerNotReadyError(f"Crane cannot manage this app ({action}): {message}")
    return InfrastructureError(f"Crane RPC {action} failed: {message}")


class CraneRpcManager:
    """A :class:`~uitest.crane.ContainerManager` over the real Crane RPC."""

    def __init__(self, device_id: str = "auto", host: str = DEFAULT_HOST, *,
                 spawn_slots: int | None = None,
                 session_factory: Callable[[str, str, int | None], Any] | None = None,
                 logger: logging.Logger | None = None) -> None:
        """``session_factory`` is injected by tests; the default opens a real
        ``CraneHostSession``."""
        self._device_id = device_id
        self._host = host
        self._spawn_slots = spawn_slots
        self._factory = session_factory or _default_session_factory
        self._log = logger or LOGGER
        self._session: Any = None
        self._detached = False

    # --- session ------------------------------------------------------------

    def _ensure_session(self) -> Any:
        if self._detached:
            raise InfrastructureError("This CraneRpcManager has been detached.")
        if self._session is None:
            try:
                self._session = self._factory(self._device_id, self._host, self._spawn_slots)
            except BaseException as error:
                raise _classify(error, "open") from error
            self._log.info("Crane host session open (device=%s host=%s)",
                           self._device_id, self._host)
        return self._session

    def _call(self, action: str, app_id: str, **kwargs) -> Any:
        session = self._ensure_session()
        try:
            return session.call(action, app_id, **kwargs)
        except BaseException as error:
            raise _classify(error, action) from error

    # --- ContainerManager ---------------------------------------------------

    def info(self, app_id: str) -> Any:
        """Crane's container info payload, as ``ContainerState.from_payload`` expects.

        A payload with ``ok: False`` is passed through unchanged -- the caller's
        ``from_payload`` already turns that into
        :class:`~uitest.errors.ContainerNotReadyError` with the payload in the
        message, which is more useful than re-wrapping it here.
        """
        return self._call("info", app_id)

    def switch(self, app_id: str, container_id: str, *, reload: bool = False) -> Any:
        """Activate a container.

        Only ``uitest.crane.prepare_container`` calls this, and only before a
        test session holds the app.
        """
        if not str(container_id or "").strip():
            raise ContainerNotReadyError("switch() needs an explicit container id.")
        return self._call("switch", app_id, container_id=container_id, reload=bool(reload))

    def detach(self) -> None:
        """Close the host session and kill the Crane host app. Idempotent."""
        if self._detached:
            return
        self._detached = True
        session = self._session
        self._session = None
        if session is None:
            return
        try:
            session.__exit__(None, None, None)
        except Exception as error:
            # Never raise out of cleanup: the caller is already shutting down.
            self._log.warning("Crane host session teardown failed: %s", error)
        else:
            self._log.info("Crane host session closed.")

    # --- convenience --------------------------------------------------------

    def active_container_id(self, app_id: str) -> str:
        """The currently active container id, or ``""`` if Crane did not report one.

        Used to stamp a snapshot with the container that produced it.
        """
        payload = self.info(app_id)
        if not isinstance(payload, dict):
            return ""
        return str(payload.get("active_container_id") or "")

    def __enter__(self) -> "CraneRpcManager":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.detach()
