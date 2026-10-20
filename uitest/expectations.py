"""Post-conditions. An action is "successful" only when one of these is observed.

The adapter has no "fire and hope" path: :meth:`UITestAdapter.tap_element`
requires an expectation, and reports success only after the expectation is
verified against a fresh, settled snapshot. A tap that dispatched cleanly but
changed nothing is a failure, not a pass.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

from .model import Snapshot
from .selectors import Selector


@runtime_checkable
class Expectation(Protocol):
    """Something observable that must become true after an action."""

    def satisfied(self, snapshot: Snapshot) -> bool: ...

    def describe(self) -> str: ...


@dataclass(frozen=True)
class ScreenIs:
    """The app is on a named screen, optionally with required elements present."""
    screen_id: str
    required: tuple[Selector, ...] = ()

    def satisfied(self, snapshot: Snapshot) -> bool:
        if snapshot.screen_id != self.screen_id:
            return False
        elements = snapshot.elements()
        return all(selector.find_all(elements) for selector in self.required)

    def describe(self) -> str:
        extra = f" with {len(self.required)} required element(s)" if self.required else ""
        return f"screen {self.screen_id!r}{extra}"


@dataclass(frozen=True)
class ElementVisible:
    selector: Selector

    def satisfied(self, snapshot: Snapshot) -> bool:
        return any(element.visible for element in self.selector.find_all(snapshot.elements()))

    def describe(self) -> str:
        return f"{self.selector.describe()} visible"


@dataclass(frozen=True)
class ElementGone:
    selector: Selector

    def satisfied(self, snapshot: Snapshot) -> bool:
        return not any(element.visible for element in self.selector.find_all(snapshot.elements()))

    def describe(self) -> str:
        return f"{self.selector.describe()} gone"


@dataclass(frozen=True)
class ElementValue:
    selector: Selector
    value: str

    def satisfied(self, snapshot: Snapshot) -> bool:
        return any((element.value or "") == self.value
                   for element in self.selector.find_all(snapshot.elements()))

    def describe(self) -> str:
        return f"{self.selector.describe()} value == {self.value!r}"


@dataclass(frozen=True)
class ElementEnabled:
    selector: Selector
    enabled: bool = True

    def satisfied(self, snapshot: Snapshot) -> bool:
        matches = self.selector.find_all(snapshot.elements())
        return bool(matches) and any(element.enabled is self.enabled for element in matches)

    def describe(self) -> str:
        return f"{self.selector.describe()} enabled == {self.enabled}"


@dataclass(frozen=True)
class AlertShowing:
    """A *known* alert is on screen (identifier or exact message)."""
    identifier: str = ""
    message: str = ""

    def satisfied(self, snapshot: Snapshot) -> bool:
        alert = snapshot.alert
        if alert is None:
            return False
        if self.identifier and alert.identifier != self.identifier:
            return False
        if self.message and alert.message != self.message:
            return False
        return True

    def describe(self) -> str:
        return f"alert {self.identifier or self.message!r} showing"


@dataclass(frozen=True)
class NoAlert:
    def satisfied(self, snapshot: Snapshot) -> bool:
        return snapshot.alert is None

    def describe(self) -> str:
        return "no alert on screen"


@dataclass(frozen=True)
class AllOf:
    parts: tuple[Expectation, ...]

    def __init__(self, *parts: Expectation) -> None:
        object.__setattr__(self, "parts", tuple(parts))

    def satisfied(self, snapshot: Snapshot) -> bool:
        return all(part.satisfied(snapshot) for part in self.parts)

    def describe(self) -> str:
        return "all of (" + "; ".join(part.describe() for part in self.parts) + ")"


@dataclass(frozen=True)
class AnyOf:
    parts: tuple[Expectation, ...]

    def __init__(self, *parts: Expectation) -> None:
        object.__setattr__(self, "parts", tuple(parts))

    def satisfied(self, snapshot: Snapshot) -> bool:
        return any(part.satisfied(snapshot) for part in self.parts)

    def describe(self) -> str:
        return "any of (" + "; ".join(part.describe() for part in self.parts) + ")"


@dataclass(frozen=True)
class BusinessResult:
    """A post-condition verified outside the UI tree.

    Use this when the UI alone cannot prove an operation landed -- for example a
    read against the test environment's own API or fixture database. The
    callable receives the snapshot and must do its own (read-only) check.
    """
    check: Callable[[Snapshot], bool]
    description: str

    def satisfied(self, snapshot: Snapshot) -> bool:
        return bool(self.check(snapshot))

    def describe(self) -> str:
        return f"business result: {self.description}"


@dataclass(frozen=True)
class ScreenSpec:
    """A screen contract for :meth:`UITestAdapter.verify_screen`."""
    screen_id: str
    required: tuple[Selector, ...] = ()
    forbidden: tuple[Selector, ...] = ()
    name: str = ""

    def describe(self) -> str:
        return self.name or self.screen_id
