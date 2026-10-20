"""Accessibility-identifier-first element selection.

There is no coordinate selector in this module, and no public coordinate tap in
the adapter. A fixed point in screen space is not an element: it survives a
layout change, a locale change and a font-size change by silently hitting
something else.
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import Element


@dataclass(frozen=True)
class Selector:
    """A declarative element query.

    ``identifier`` matches ``accessibilityIdentifier``, which app authors control
    and translators do not. ``label``/``value``/``placeholder`` are fallbacks for
    screens that ship without identifiers; they are locale-dependent, so
    :attr:`uses_fallback` is True for them and the adapter logs it.

    ``index`` is a deterministic tiebreak for genuinely repeated elements (a
    keypad, a table of identical rows) and is applied in document order. It is
    never a substitute for an identifier.
    """
    identifier: str | None = None
    type: str | None = None
    label: str | None = None
    value: str | None = None
    placeholder: str | None = None
    index: int | None = None

    def __post_init__(self) -> None:
        if not any((self.identifier, self.type, self.label, self.value, self.placeholder)):
            raise ValueError("Selector needs at least one of identifier/type/label/value/placeholder.")
        if self.index is not None and self.index < 0:
            raise ValueError("Selector index must be >= 0.")

    # --- constructors -------------------------------------------------------

    @classmethod
    def by_id(cls, identifier: str, *, type: str | None = None, index: int | None = None) -> "Selector":
        return cls(identifier=identifier, type=type, index=index)

    @classmethod
    def by_label(cls, label: str, *, type: str | None = None, index: int | None = None) -> "Selector":
        """Locale-dependent fallback. Prefer :meth:`by_id`."""
        return cls(label=label, type=type, index=index)

    @classmethod
    def by_placeholder(cls, placeholder: str, *, type: str | None = None) -> "Selector":
        """Locale-dependent fallback. Prefer :meth:`by_id`."""
        return cls(placeholder=placeholder, type=type)

    # --- matching -----------------------------------------------------------

    @property
    def uses_fallback(self) -> bool:
        return not self.identifier

    def matches(self, element: Element) -> bool:
        if self.identifier is not None and element.identifier != self.identifier:
            return False
        if self.type is not None and element.type != self.type:
            return False
        if self.label is not None and element.label != self.label:
            return False
        if self.value is not None and (element.value or "") != self.value:
            return False
        if self.placeholder is not None and element.placeholder != self.placeholder:
            return False
        return True

    def find_all(self, elements) -> tuple[Element, ...]:
        return tuple(element for element in elements if self.matches(element))

    def describe(self) -> str:
        parts = [f"{key}={value!r}" for key, value in (
            ("id", self.identifier), ("type", self.type), ("label", self.label),
            ("value", self.value), ("placeholder", self.placeholder), ("index", self.index),
        ) if value is not None]
        return "Selector(" + ", ".join(parts) + ")"
