"""Crane container lifecycle for a UI test run.

The rule this module enforces: **switching happens before the session, never
inside it.** A container switch mid-test invalidates every assumption a test has
already made -- the data it created, the logged-in state it observed, the screen
it is standing on -- so the session treats a changed active container as a hard
failure rather than adapting to it.

Split of responsibility:

* :func:`prepare_container` -- selects/activates a container. Refused while any
  session holds that app.
* :class:`CraneContainerSession` -- verifies readiness, binds the run to one
  container id, re-checks it before every action, and releases/detaches at the
  end.

Out of scope on purpose: this module reads and selects containers. It does not
touch the Keychain, device identifiers, advertising identifiers or any other
fingerprint surface, and it exposes no API for doing so.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Protocol, runtime_checkable

from .errors import ContainerChangedError, ContainerNotReadyError, SessionClosedError

LOGGER = logging.getLogger("uitest.crane")

# app_id -> container_id currently held by an open session in this process.
_HELD: dict[str, str] = {}


@dataclass(frozen=True)
class ContainerState:
    """A read of Crane's view of one app."""
    app_id: str
    supported: bool
    active_container_id: str
    active_container_name: str = ""
    containers: tuple[str, ...] = ()

    @classmethod
    def from_payload(cls, payload: object) -> "ContainerState":
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise ContainerNotReadyError(f"Crane did not return a usable container state: {payload!r}")
        return cls(
            app_id=str(payload.get("app_id") or ""),
            supported=bool(payload.get("supported", False)),
            active_container_id=str(payload.get("active_container_id") or ""),
            active_container_name=str(payload.get("active_container_name") or ""),
            containers=tuple(str(item) for item in payload.get("containers", []) if str(item)),
        )

    def describe(self) -> str:
        label = self.active_container_name or self.active_container_id or "<none>"
        return f"{self.app_id} active={label} ({len(self.containers)} container(s))"


@runtime_checkable
class ContainerManager(Protocol):
    """The subset of a Crane RPC client this module needs."""

    def info(self, app_id: str) -> object:
        """Return Crane's container info payload for ``app_id``."""
        ...

    def switch(self, app_id: str, container_id: str, *, reload: bool = False) -> object:
        """Activate a container. Called only by :func:`prepare_container`."""
        ...

    def detach(self) -> None:
        """Release the RPC session. Idempotent."""
        ...


def prepare_container(manager: ContainerManager, app_id: str, container_id: str,
                      *, reload: bool = False) -> ContainerState:
    """Activate ``container_id`` and confirm Crane reports it active.

    Call this *before* opening a :class:`CraneContainerSession`. It refuses while
    a session in this process holds the app, which is what keeps a parallel
    worker from pulling the container out from under a running test.
    """
    held = _HELD.get(app_id)
    if held is not None:
        raise ContainerNotReadyError(
            f"A test session already holds {app_id} on container {held!r}; refusing to switch "
            "containers while a test is running."
        )
    if not container_id:
        raise ContainerNotReadyError("prepare_container needs an explicit container id.")
    state = ContainerState.from_payload(manager.info(app_id))
    if not state.supported:
        raise ContainerNotReadyError(f"Crane reports {app_id} is not container-managed.")
    if container_id not in state.containers:
        raise ContainerNotReadyError(
            f"Container {container_id!r} does not exist for {app_id}. Known: {list(state.containers)}"
        )
    if state.active_container_id != container_id:
        manager.switch(app_id, container_id, reload=reload)
        state = ContainerState.from_payload(manager.info(app_id))
    if state.active_container_id != container_id:
        raise ContainerNotReadyError(
            f"Crane did not activate {container_id!r} for {app_id}; active is "
            f"{state.active_container_id!r}."
        )
    LOGGER.info("Container ready: %s", state.describe())
    return state


class CraneContainerSession:
    """Binds one test run to one container and refuses mid-run changes."""

    def __init__(self, manager: ContainerManager, app_id: str, container_id: str, *,
                 recheck_interval: float = 0.0, clock=time.monotonic,
                 logger: logging.Logger | None = None) -> None:
        """``recheck_interval`` throttles the pre-action verification: 0 checks
        on every action (the safe default), a positive value caches the last
        good read for that many seconds on slow transports."""
        self._manager = manager
        self._app_id = app_id
        self._container_id = container_id
        self._recheck_interval = max(0.0, float(recheck_interval))
        self._clock = clock
        self._log = logger or LOGGER
        self._opened = False
        self._closed = False
        self._last_check = 0.0
        self.state: ContainerState | None = None

    @property
    def app_id(self) -> str:
        return self._app_id

    @property
    def container_id(self) -> str:
        return self._container_id

    def open(self) -> ContainerState:
        """Verify the pre-selected container is active, then hold it.

        Does not switch: an unexpected active container is an error, because the
        caller's idea of which account/profile is under test would otherwise be
        silently wrong.
        """
        if self._closed:
            raise SessionClosedError("This container session has been closed.")
        if self._opened:
            raise ContainerNotReadyError("This container session is already open.")
        held = _HELD.get(self._app_id)
        if held is not None:
            raise ContainerNotReadyError(
                f"Another session already holds {self._app_id} on container {held!r}."
            )
        state = ContainerState.from_payload(self._manager.info(self._app_id))
        if not state.supported:
            raise ContainerNotReadyError(f"Crane reports {self._app_id} is not container-managed.")
        if not self._container_id:
            raise ContainerNotReadyError(
                "No container was selected for this run. Select one with prepare_container() first; "
                "the session will not pick one for you."
            )
        if self._container_id not in state.containers:
            raise ContainerNotReadyError(
                f"Container {self._container_id!r} does not exist for {self._app_id}. "
                f"Known: {list(state.containers)}"
            )
        if state.active_container_id != self._container_id:
            raise ContainerNotReadyError(
                f"Expected container {self._container_id!r} to be active before the test starts, "
                f"but {state.active_container_id!r} is active. Run prepare_container() first; the "
                "test session does not switch containers."
            )
        self._opened = True
        self._last_check = self._clock()
        self.state = state
        _HELD[self._app_id] = self._container_id
        self._log.info("Container session open: %s", state.describe())
        return state

    def assert_stable(self) -> None:
        """Re-read Crane and fail if the active container moved.

        Called by the adapter before every mutating action.
        """
        if self._closed:
            raise SessionClosedError("This container session has been closed.")
        if not self._opened:
            raise ContainerNotReadyError("Container session was never opened.")
        now = self._clock()
        if self._recheck_interval and (now - self._last_check) < self._recheck_interval:
            return
        state = ContainerState.from_payload(self._manager.info(self._app_id))
        if state.active_container_id != self._container_id:
            raise ContainerChangedError(
                f"Active container changed mid-test: expected {self._container_id!r}, now "
                f"{state.active_container_id!r}. Stopping; the results so far belong to a "
                "different container."
            )
        self._last_check = now
        self.state = state

    def close(self) -> None:
        """Release the hold and detach the RPC session. Idempotent."""
        if self._closed:
            return
        self._closed = True
        if self._opened and _HELD.get(self._app_id) == self._container_id:
            _HELD.pop(self._app_id, None)
        self._opened = False
        try:
            self._manager.detach()
        except Exception as error:
            self._log.warning("Crane detach failed (continuing): %s", error)
        else:
            self._log.info("Container session closed: %s/%s", self._app_id, self._container_id)

    def __enter__(self) -> "CraneContainerSession":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
