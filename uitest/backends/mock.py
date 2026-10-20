"""A deterministic in-memory UI for tests.

:class:`FakeApp` is a tiny screen/state machine; :class:`MockBackend` adapts it
to the :class:`~uitest.backends.base.Backend` protocol. Together they let the
whole adapter -- waits, settling, recovery, verification -- run in unit tests
with no device, no Frida and no network.

It models the failure modes that matter: animation (frames that move for N
reads), keyboard occlusion, elements outside the window, screens that change
under the test, and a transport that throws :class:`InfrastructureError`.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable

from ..errors import InfrastructureError
from ..model import Alert, Element, Rect, Snapshot

WINDOW = Rect(0, 0, 390, 844)
KEYBOARD = Rect(0, 500, 390, 344)


@dataclass
class FakeElement:
    identifier: str
    type: str = "Button"
    label: str = ""
    value: str | None = None
    placeholder: str = ""
    enabled: bool = True
    visible: bool = True
    hittable: bool = True
    focused: bool = False
    protected: bool = False
    frame: Rect = Rect(20, 100, 350, 44)

    def to_element(self) -> Element:
        return Element(
            ref=self.identifier or f"{self.type}:{self.label}", type=self.type,
            identifier=self.identifier, label=self.label, value=self.value,
            placeholder=self.placeholder, enabled=self.enabled, visible=self.visible,
            hittable=self.hittable, focused=self.focused, protected=self.protected,
            frame=self.frame,
        )


@dataclass
class FakeScreen:
    screen_id: str
    elements: list[FakeElement] = field(default_factory=list)
    alert: Alert | None = None
    keyboard: bool = False

    def find(self, identifier: str) -> FakeElement | None:
        for element in self.elements:
            if element.identifier == identifier:
                return element
        return None


TapHandler = Callable[["FakeApp", FakeElement], None]


class FakeApp:
    """A scripted app: screens, transitions and an animation counter."""

    def __init__(self, bundle_id: str, screens: list[FakeScreen], *, current: str = "",
                 container_id: str = "profile-1") -> None:
        self.bundle_id = bundle_id
        self.screens = {screen.screen_id: screen for screen in screens}
        self.current = current or screens[0].screen_id
        self.container_id = container_id
        self.taps: list[tuple[str, str]] = []
        self.typed: list[tuple[str, str]] = []
        self.cleared: list[str] = []
        self.scrolled: list[str] = []
        self.keyboard_dismissals = 0
        self.alert_taps: list[str] = []
        self.closed = False
        # Reads remaining during which the screen keeps moving (animation).
        self.moving_reads = 0
        self._offset = 0.0
        self._tap_handlers: dict[tuple[str, str], TapHandler] = {}
        self._type_handlers: dict[tuple[str, str], Callable[["FakeApp", FakeElement, str], None]] = {}
        self._reads = 0

    # --- scripting ----------------------------------------------------------

    def on_tap(self, screen_id: str, identifier: str, handler: TapHandler) -> "FakeApp":
        self._tap_handlers[(screen_id, identifier)] = handler
        return self

    def on_type(self, screen_id: str, identifier: str,
                handler: Callable[["FakeApp", FakeElement, str], None]) -> "FakeApp":
        self._type_handlers[(screen_id, identifier)] = handler
        return self

    def navigates(self, screen_id: str, identifier: str, destination: str) -> "FakeApp":
        """Tapping ``identifier`` on ``screen_id`` goes to ``destination``."""
        def handler(app: "FakeApp", _element: FakeElement) -> None:
            app.goto(destination)
        return self.on_tap(screen_id, identifier, handler)

    def goto(self, screen_id: str, *, animate_reads: int = 0) -> None:
        if screen_id not in self.screens:
            raise KeyError(f"Unknown screen {screen_id!r}")
        self.current = screen_id
        self.moving_reads = max(self.moving_reads, animate_reads)

    @property
    def screen(self) -> FakeScreen:
        return self.screens[self.current]

    @property
    def reads(self) -> int:
        return self._reads

    # --- reading ------------------------------------------------------------

    def capture(self, clock: Callable[[], float]) -> Snapshot:
        self._reads += 1
        screen = self.screen
        if self.moving_reads > 0:
            self.moving_reads -= 1
            self._offset += 7.0
        else:
            self._offset = 0.0
        children = []
        for fake in screen.elements:
            element = fake.to_element()
            if self._offset:
                element = replace(element, frame=Rect(
                    fake.frame.x, fake.frame.y + self._offset, fake.frame.width, fake.frame.height))
            children.append(element)
        root = Element(ref="window", type="Window", identifier="", frame=WINDOW,
                       children=tuple(children))
        return Snapshot(
            bundle_id=self.bundle_id, screen_id=screen.screen_id, root=root, window=WINDOW,
            captured_at=clock(), alert=screen.alert,
            keyboard=KEYBOARD if screen.keyboard else None,
            container_id=self.container_id,
        )

    # --- acting -------------------------------------------------------------

    def tap(self, ref: str) -> None:
        screen = self.screen
        element = screen.find(ref)
        if element is None:
            raise AssertionError(f"MockBackend tapped {ref!r}, absent from {screen.screen_id!r}")
        self.taps.append((screen.screen_id, ref))
        handler = self._tap_handlers.get((screen.screen_id, ref))
        if handler is not None:
            handler(self, element)

    def type_text(self, ref: str, text: str) -> None:
        screen = self.screen
        element = screen.find(ref)
        if element is None:
            raise AssertionError(f"MockBackend typed into {ref!r}, absent from {screen.screen_id!r}")
        self.typed.append((ref, text))
        element.focused = True
        if not element.protected:
            element.value = (element.value or "") + text
        handler = self._type_handlers.get((screen.screen_id, ref))
        if handler is not None:
            handler(self, element, text)

    def clear_text(self, ref: str) -> None:
        element = self.screen.find(ref)
        if element is not None:
            self.cleared.append(ref)
            element.value = ""

    def scroll_into_view(self, ref: str) -> None:
        self.scrolled.append(ref)

    def dismiss_keyboard(self) -> None:
        self.keyboard_dismissals += 1
        self.screen.keyboard = False


class MockBackend:
    """Adapts :class:`FakeApp` to the backend protocol, with injectable faults."""

    def __init__(self, app: FakeApp, *, clock: Callable[[], float] = lambda: 0.0,
                 read_faults: int = 0, fault_message: str = "mock transport fault") -> None:
        self._app = app
        self._clock = clock
        self._read_faults = int(read_faults)
        self._fault_message = fault_message
        self.capture_calls = 0

    @property
    def name(self) -> str:
        return "mock"

    @property
    def app(self) -> FakeApp:
        return self._app

    def capture(self) -> Snapshot:
        self.capture_calls += 1
        if self._read_faults > 0:
            self._read_faults -= 1
            raise InfrastructureError(self._fault_message)
        return self._app.capture(self._clock)

    def tap(self, ref: str) -> None:
        self._app.tap(ref)

    def type_text(self, ref: str, text: str) -> None:
        self._app.type_text(ref, text)

    def clear_text(self, ref: str) -> None:
        self._app.clear_text(ref)

    def scroll_into_view(self, ref: str) -> None:
        self._app.scroll_into_view(ref)

    def dismiss_keyboard(self) -> None:
        self._app.dismiss_keyboard()

    def tap_alert_button(self, label: str) -> None:
        screen = self._app.screen
        alert = screen.alert
        if alert is None or label not in alert.buttons:
            raise AssertionError(f"No alert button {label!r} on {screen.screen_id!r}")
        self._app.alert_taps.append(label)
        screen.alert = None

    def close(self) -> None:
        self._app.closed = True


class FakeClock:
    """Monotonic clock that only advances when the code under test sleeps."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(float(seconds))
        self.now += float(seconds)

    @property
    def total_slept(self) -> float:
        return sum(self.slept)


class FakeContainerManager:
    """A Crane RPC double: scripted container state, switch and detach."""

    def __init__(self, app_id: str, containers: tuple[str, ...], active: str, *,
                 supported: bool = True, ok: bool = True) -> None:
        self.app_id = app_id
        self.containers = list(containers)
        self.active = active
        self.supported = supported
        self.ok = ok
        self.switches: list[tuple[str, bool]] = []
        self.detached = 0
        self.info_calls = 0

    def info(self, app_id: str) -> dict:
        self.info_calls += 1
        return {
            "ok": self.ok, "app_id": app_id, "supported": self.supported,
            "active_container_id": self.active, "active_container_name": f"Profile {self.active}",
            "containers": list(self.containers),
        }

    def switch(self, app_id: str, container_id: str, *, reload: bool = False) -> dict:
        self.switches.append((container_id, reload))
        self.active = container_id
        return {"ok": True, "app_id": app_id, "active_container_id": container_id}

    def detach(self) -> None:
        self.detached += 1
