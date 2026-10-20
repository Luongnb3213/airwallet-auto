"""The backend contract.

A backend translates a UI-test framework into :mod:`uitest.model` types. Note
what is *absent* from this protocol, on purpose:

* no ``tap_coordinate`` / ``tap_point`` -- the adapter cannot tap a bare point;
* no ``send_action`` / ``invoke_selector`` -- no target/action dispatch, no
  ``insertText:``, no private-selector calls;
* no ``set_value`` that writes a field's backing store directly.

Every mutating method here is expected to go through the test framework's event
path (``XCUIElement.tap()``, ``typeText()``), so the app sees the same
touch/keyboard pipeline it sees from a real user's tap, minus the hardware and
the human. A backend that cannot provide that should not implement this
protocol.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..model import Snapshot


@runtime_checkable
class Backend(Protocol):
    """Read and act on the app under test.

    Implementations raise :class:`uitest.errors.InfrastructureError` for
    transport faults (session dropped, HTTP error, agent not responding) and
    :class:`uitest.errors.UITestError` subclasses for UI-state problems. The
    distinction drives retry policy, so it must not be blurred.
    """

    @property
    def name(self) -> str:
        """Short backend name for logs."""
        ...

    def capture(self) -> Snapshot:
        """Read the current UI state. Read-only, therefore retryable."""
        ...

    def tap(self, ref: str) -> None:
        """Tap an element through the framework's event path. Never retried."""
        ...

    def type_text(self, ref: str, text: str) -> None:
        """Type into the focused element through the framework's keyboard path."""
        ...

    def clear_text(self, ref: str) -> None:
        """Clear a text element's content through the framework's editing path."""
        ...

    def scroll_into_view(self, ref: str) -> None:
        """Bring an element into the interactable area. One bounded attempt."""
        ...

    def dismiss_keyboard(self) -> None:
        """Dismiss the software keyboard through the framework (Return/Done)."""
        ...

    def tap_alert_button(self, label: str) -> None:
        """Tap a button on the presented alert, by exact label."""
        ...

    def close(self) -> None:
        """Release the automation session. Idempotent."""
        ...
