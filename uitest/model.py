"""Immutable UI state model shared by every backend.

A backend's only job is to translate its native tree (XCUITest element snapshot,
WebDriverAgent JSON, a fake) into these types. Everything above this layer is
backend-agnostic and never touches coordinates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Iterator


def _round(value: float) -> float:
    """Quantise to half-pixels so sub-pixel jitter does not look like animation."""
    return round(float(value) * 2) / 2


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    def intersects(self, other: "Rect") -> bool:
        if self.empty or other.empty:
            return False
        return (self.x < other.right and other.x < self.right
                and self.y < other.bottom and other.y < self.bottom)

    def contains(self, other: "Rect") -> bool:
        return (self.x <= other.x and self.y <= other.y
                and self.right >= other.right and self.bottom >= other.bottom)

    def quantised(self) -> tuple[float, float, float, float]:
        return (_round(self.x), _round(self.y), _round(self.width), _round(self.height))


@dataclass(frozen=True)
class Element:
    """One node of the accessibility tree.

    ``ref`` is an opaque backend handle. The adapter passes it back to the
    backend to act on the element; it never interprets it, and never derives a
    tap coordinate from ``frame``.
    """
    ref: str
    type: str
    identifier: str = ""
    label: str = ""
    value: str | None = None
    placeholder: str = ""
    enabled: bool = True
    visible: bool = True
    hittable: bool = True
    focused: bool = False
    protected: bool = False
    frame: Rect = Rect(0, 0, 0, 0)
    children: tuple["Element", ...] = ()

    def walk(self) -> Iterator["Element"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def describe(self) -> str:
        name = self.identifier or self.label or self.placeholder or "<unnamed>"
        return f"{self.type}[{name}]"


@dataclass(frozen=True)
class Alert:
    """A modal presented by the app or by the system."""
    identifier: str = ""
    title: str = ""
    message: str = ""
    buttons: tuple[str, ...] = ()
    system: bool = False

    def describe(self) -> str:
        where = "system" if self.system else "app"
        return f"{where} alert {self.identifier or self.title!r}: {self.message!r} {list(self.buttons)}"


@dataclass(frozen=True)
class ScreenToken:
    """Screen identity, stable across data entry but not across navigation.

    Built from the screen id plus the set of element identifiers/types, and
    deliberately *not* from values or frames, so that typing into a field or
    finishing an animation does not read as "the screen changed".
    """
    bundle_id: str
    screen_id: str
    structure: str

    def describe(self) -> str:
        return f"{self.bundle_id}/{self.screen_id}#{self.structure[:8]}"


@dataclass(frozen=True)
class Snapshot:
    """One read of the app's UI state."""
    bundle_id: str
    screen_id: str
    root: Element
    window: Rect
    captured_at: float = 0.0
    alert: Alert | None = None
    keyboard: Rect | None = None
    container_id: str = ""
    _elements: tuple[Element, ...] = field(default=(), repr=False, compare=False)

    def elements(self) -> tuple[Element, ...]:
        if self._elements:
            return self._elements
        return tuple(self.root.walk())

    def token(self) -> ScreenToken:
        shape = sorted(
            f"{element.type}|{element.identifier}"
            for element in self.elements()
            if element.identifier or element.type
        )
        digest = hashlib.sha256(json.dumps(shape, ensure_ascii=False).encode()).hexdigest()
        return ScreenToken(self.bundle_id, self.screen_id, digest)

    def fingerprint(self) -> str:
        """Hash of everything that moves. Two equal fingerprints == a still screen."""
        payload = {
            "screen": self.screen_id,
            "alert": None if self.alert is None else [self.alert.identifier, self.alert.title,
                                                      self.alert.message, list(self.alert.buttons)],
            "keyboard": None if self.keyboard is None else list(self.keyboard.quantised()),
            "elements": [
                [element.type, element.identifier, element.label, element.value,
                 element.enabled, element.visible, element.hittable, element.focused,
                 list(element.frame.quantised())]
                for element in self.elements()
            ],
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
