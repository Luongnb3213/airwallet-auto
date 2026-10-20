"""Error taxonomy for the UI testing adapter.

The split matters for timing policy: only :class:`InfrastructureError` is ever
retried with backoff, because a retry of a *mutating* UI action can double-submit
a business operation. Everything else halts the test.
"""
from __future__ import annotations


class UITestError(Exception):
    """Base class for every adapter failure."""


# --- Policy / authorization -------------------------------------------------

class TargetNotAuthorizedError(UITestError):
    """The app under test is not a declared, licensed test target."""


class BusinessActionNotAuthorizedError(UITestError):
    """An action flagged as a real business write was attempted on a target that
    does not permit business writes (``allow_business_writes=False``)."""


class UnexpectedAlertError(UITestError):
    """A modal appeared that no policy rule recognizes.

    The adapter never guesses a button on an unknown alert; it stops and keeps
    the alert on screen for a human to read.
    """

    def __init__(self, message: str, *, alert: object = None) -> None:
        super().__init__(message)
        self.alert = alert


# --- Element resolution -----------------------------------------------------

class ElementNotFoundError(UITestError):
    """No element matched the selector."""


class AmbiguousSelectorError(UITestError):
    """More than one element matched and no deterministic tiebreak was given."""


class ElementNotInteractableError(UITestError):
    """The element exists but cannot legitimately receive a test-framework event."""

    def __init__(self, message: str, *, reason: str = "") -> None:
        super().__init__(message)
        self.reason = reason


class KeyboardObstructionError(ElementNotInteractableError):
    """The software keyboard covers the element's frame."""

    def __init__(self, message: str) -> None:
        super().__init__(message, reason="keyboard_obstruction")


# --- Timing / state ---------------------------------------------------------

class WaitTimeoutError(UITestError):
    """A bounded wait expired before its condition held."""

    def __init__(self, message: str, *, description: str = "", elapsed: float = 0.0) -> None:
        super().__init__(message)
        self.description = description
        self.elapsed = elapsed


class NotSettledError(WaitTimeoutError):
    """The screen never stopped changing within the settle budget (animation ran long)."""


class ScreenChangedError(UITestError):
    """The screen identity changed between the pre-action check and the action."""

    def __init__(self, message: str, *, expected: object = None, actual: object = None) -> None:
        super().__init__(message)
        self.expected = expected
        self.actual = actual


class ScreenVerificationError(UITestError):
    """``verify_screen`` found the screen does not match its declared contract."""

    def __init__(self, message: str, *, missing: tuple = (), unexpected: tuple = ()) -> None:
        super().__init__(message)
        self.missing = missing
        self.unexpected = unexpected


class ExpectationFailedError(UITestError):
    """An action ran but its declared post-condition was never observed."""

    def __init__(self, message: str, *, description: str = "") -> None:
        super().__init__(message)
        self.description = description


class UnverifiableInputError(UITestError):
    """Text was requested on a field whose content cannot be read back.

    Secure fields return no value to the accessibility tree, so the caller must
    supply an observable post-condition instead of assuming success.
    """


class RepeatedActionError(UITestError):
    """The same action was requested twice on the same screen without a state change."""


# --- Container lifecycle ----------------------------------------------------

class ContainerError(UITestError):
    """Base class for Crane container lifecycle failures."""


class ContainerNotReadyError(ContainerError):
    """The container manager is unavailable, or the expected container is not active."""


class ContainerChangedError(ContainerError):
    """The active container changed while a test session held it."""


class SessionClosedError(UITestError):
    """The adapter or container session was used after it was closed."""


# --- Transport --------------------------------------------------------------

class InfrastructureError(UITestError):
    """A transport/automation-session fault, unrelated to the app's UI state.

    The only error class eligible for bounded backoff retry, and only on
    read-only calls.
    """
