"""The UI testing adapter.

Six public operations -- :meth:`UITestAdapter.snapshot`,
:meth:`~UITestAdapter.find_element`, :meth:`~UITestAdapter.tap_element`,
:meth:`~UITestAdapter.type_text`, :meth:`~UITestAdapter.wait_until_visible`,
:meth:`~UITestAdapter.verify_screen` -- plus :meth:`~UITestAdapter.dismiss_alert`
for alerts that policy declares in advance.

Invariants every mutating operation holds:

1. the app under test is a declared, licensed test target;
2. the Crane container bound at session start is still the active one;
3. no undeclared alert is on screen;
4. the screen has stopped animating;
5. the element resolves to exactly one node, found by identifier where possible,
   and is genuinely interactable (enabled, visible, hittable, inside the
   interactable area, not under the keyboard);
6. the screen identity is unchanged between that check and the dispatch;
7. the declared post-condition is observed on a fresh, settled snapshot --
   otherwise the operation failed, no matter how cleanly the event dispatched;
8. the same action is not repeated on the same screen unless the caller said it
   is repeatable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import logging
import time

from .backends.base import Backend
from .errors import (
    AmbiguousSelectorError,
    BusinessActionNotAuthorizedError,
    ContainerChangedError,
    ElementNotFoundError,
    ElementNotInteractableError,
    ExpectationFailedError,
    KeyboardObstructionError,
    RepeatedActionError,
    ScreenChangedError,
    ScreenVerificationError,
    SessionClosedError,
    TargetNotAuthorizedError,
    UITestError,
    UnexpectedAlertError,
    UnverifiableInputError,
    WaitTimeoutError,
)
from .expectations import ElementValue, Expectation, ScreenSpec
from .model import Element, ScreenToken, Snapshot
from .policy import AlertPolicy, AuthorizedTarget
from .selectors import Selector
from .waits import Budget, Clock, Sleep, Waiter, retry_read

LOGGER = logging.getLogger("uitest.adapter")


@dataclass(frozen=True)
class ActionRecord:
    """One attempted interaction, for the run report."""
    verb: str
    selector: str
    screen_id: str
    token: str
    expectation: str
    elapsed: float
    recovered: str = ""
    fallback_selector: bool = False
    container_id: str = ""


@dataclass
class _ActionKey:
    verb: str
    selector: str
    token: str
    payload: str = ""

    def as_tuple(self) -> tuple[str, str, str, str]:
        return (self.verb, self.selector, self.token, self.payload)


class UITestAdapter:
    """Backend-agnostic UI automation for a licensed test build."""

    def __init__(self, backend: Backend, *, target: AuthorizedTarget,
                 budget: Budget | None = None, alert_policy: AlertPolicy | None = None,
                 container=None, clock: Clock = time.monotonic, sleep: Sleep = time.sleep,
                 logger: logging.Logger | None = None) -> None:
        self._backend = backend
        self._target = target
        self._budget = budget or Budget()
        self._alerts = alert_policy or AlertPolicy()
        self._container = container
        self._clock = clock
        self._sleep = sleep
        self._log = logger or LOGGER
        self._closed = False
        self._secrets: set[str] = set()
        self._performed: set[tuple[str, str, str, str]] = set()
        self.history: list[ActionRecord] = []
        self._waiter = Waiter(self._capture, budget=self._budget, clock=clock, sleep=sleep)

    # --- lifecycle ----------------------------------------------------------

    @property
    def target(self) -> AuthorizedTarget:
        return self._target

    @property
    def budget(self) -> Budget:
        return self._budget

    def close(self) -> None:
        """Release the automation session and the container binding. Idempotent."""
        if self._closed:
            return
        self._closed = True
        errors: list[str] = []
        try:
            self._backend.close()
        except Exception as error:  # the session is going away either way
            errors.append(f"backend: {error}")
        if self._container is not None:
            try:
                self._container.close()
            except Exception as error:
                errors.append(f"container: {error}")
        if errors:
            self._log.warning("Cleanup finished with errors: %s", "; ".join(errors))

    def __enter__(self) -> "UITestAdapter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def register_secret(self, value: str) -> None:
        """Keep ``value`` out of exception messages and logs."""
        if value:
            self._secrets.add(value)

    # --- reading ------------------------------------------------------------

    def snapshot(self, *, settled: bool = False) -> Snapshot:
        """Read the current UI state.

        Pure observation: an undeclared alert is *reported* in the snapshot
        rather than raised, so a failing run can be diagnosed from a dump. The
        guards that refuse to act on an unknown alert live on the mutating
        paths.
        """
        self._require_open()
        if settled:
            return self._waiter.settle(description="a snapshot")
        return self._capture()

    def find_element(self, selector: Selector, *, snapshot: Snapshot | None = None,
                     require_interactable: bool = False, recover: bool = False) -> Element:
        """Resolve ``selector`` to exactly one element.

        Zero matches raises :class:`ElementNotFoundError`; two or more raises
        :class:`AmbiguousSelectorError` unless the selector carries an ``index``.
        """
        self._require_open()
        snapshot = snapshot if snapshot is not None else self._capture()
        element = self._resolve(selector, snapshot)
        if require_interactable:
            element, _ = self._ensure_interactable(selector, element, snapshot, recover=recover)
        return element

    def wait_until_visible(self, selector: Selector, *, timeout: float | None = None,
                           interactable: bool = False) -> Element:
        """Wait for the element to exist and be visible, within the budget.

        This is the replacement for a fixed sleep after navigation: it returns as
        soon as the element is ready, and raises :class:`WaitTimeoutError` at the
        deadline rather than carrying on regardless.
        """
        self._require_open()
        snapshot = self._await_present(selector, timeout=timeout, interactable=interactable)
        return self._resolve(selector, snapshot)

    def verify_screen(self, spec: ScreenSpec, *, timeout: float | None = None) -> Snapshot:
        """Assert the app is on the screen ``spec`` describes.

        Waits for the screen to settle, then checks identity, required elements
        and forbidden elements, and reports the exact diff on failure.
        """
        self._require_open()
        self._assert_container()

        def matches(snapshot: Snapshot) -> bool:
            if snapshot.screen_id != spec.screen_id:
                return False
            missing, unexpected = self._screen_diff(spec, snapshot)
            return not missing and not unexpected

        try:
            snapshot = self._waiter.poll(matches, description=f"screen {spec.describe()}",
                                         timeout=timeout, inspect=self._reject_unknown_alert)
        except WaitTimeoutError as error:
            latest = self._capture()
            missing, unexpected = self._screen_diff(spec, latest)
            raise ScreenVerificationError(
                f"Screen {spec.describe()!r} not verified after {error.elapsed:.2f}s: "
                f"screen_id={latest.screen_id!r} (expected {spec.screen_id!r}), "
                f"missing={[item.describe() for item in missing]}, "
                f"unexpected={[item.describe() for item in unexpected]}",
                missing=missing, unexpected=unexpected,
            ) from error
        return self._waiter.settle(description=f"screen {spec.describe()}",
                                   inspect=self._reject_unknown_alert)

    # --- acting -------------------------------------------------------------

    def tap_element(self, selector: Selector, *, expect: Expectation,
                    timeout: float | None = None, recover: bool = True,
                    allow_repeat: bool = False, business_write: bool = False) -> Snapshot:
        """Tap an element and return the settled snapshot that proves it worked.

        ``expect`` is mandatory. A tap whose post-condition is never observed
        raises :class:`ExpectationFailedError`; the adapter does not report
        success merely because the event dispatched.

        Set ``allow_repeat`` for genuinely repeatable controls (a keypad digit, a
        stepper). Set ``business_write`` for a tap that completes a real business
        operation; it is refused unless the target permits business writes.
        """
        self._authorize_business(business_write, f"tap {selector.describe()}")
        started = self._clock()
        settled, element, recovered = self._prepare(selector, recover=recover, timeout=timeout)
        key = _ActionKey("tap", selector.describe(), settled.token().structure)
        self._guard_repeat(key, allow_repeat=allow_repeat, what=f"tap {selector.describe()}")
        confirmed = self._confirm_unchanged(selector, settled, recover=False)
        self._log.info("tap %s on screen %s", selector.describe(), settled.screen_id)
        self._backend.tap(confirmed.ref)
        snapshot = self._await_expectation(expect, timeout=timeout,
                                           what=f"tap {selector.describe()}")
        self._record("tap", selector, settled, expect, started, recovered)
        return snapshot

    def type_text(self, selector: Selector, text: str, *, expect: Expectation | None = None,
                  timeout: float | None = None, clear: bool = True, secret: bool = False,
                  recover: bool = True, allow_repeat: bool = False) -> Snapshot:
        """Type ``text`` into a text element through the framework's keyboard path.

        Verification: by default the adapter reads the field back and requires it
        to equal ``text``. A secure field returns no value to the accessibility
        tree, so for ``secret=True`` (or a field the backend reports as
        ``protected``) the caller must supply ``expect`` -- some other observable
        consequence, such as the submit button becoming enabled. Without it the
        adapter raises :class:`UnverifiableInputError` rather than assume success.
        """
        if secret:
            self.register_secret(text)
        started = self._clock()
        try:
            settled, element, recovered = self._prepare(selector, recover=recover, timeout=timeout)
            if (element.protected or secret) and expect is None:
                raise UnverifiableInputError(
                    f"{selector.describe()} is a secure field: its value cannot be read back. "
                    "Pass expect= with an observable consequence (for example the continue "
                    "button becoming enabled)."
                )
            expectation: Expectation = expect if expect is not None else ElementValue(selector, text)
            key = _ActionKey("type", selector.describe(), settled.token().structure, _digest(text))
            self._guard_repeat(key, allow_repeat=allow_repeat, what=f"type into {selector.describe()}")
            confirmed = self._confirm_unchanged(selector, settled, recover=False)
            self._log.info("type into %s on screen %s (%d chars%s)", selector.describe(),
                           settled.screen_id, len(text), ", secret" if secret or element.protected else "")
            if clear:
                self._backend.clear_text(confirmed.ref)
            self._backend.type_text(confirmed.ref, text)
            snapshot = self._await_expectation(expectation, timeout=timeout,
                                               what=f"type into {selector.describe()}")
            self._record("type", selector, settled, expectation, started, recovered)
            return snapshot
        except UITestError as error:
            raise self._scrub(error) from None

    def dismiss_alert(self, *, expect: Expectation, timeout: float | None = None) -> Snapshot:
        """Dismiss the presented alert using the button its policy rule names.

        Only alerts declared in :class:`~uitest.policy.AlertPolicy` can be
        dismissed, and only through the labelled button. An undeclared alert --
        or one matched by two rules -- halts the run with the alert untouched.
        """
        self._require_open()
        self._assert_container()
        snapshot = self._waiter.settle(description="the alert")
        alert = snapshot.alert
        if alert is None:
            raise UITestError("dismiss_alert called with no alert on screen.")
        rule = self._alerts.classify(alert)
        if rule is None:
            raise UnexpectedAlertError(
                "No single policy rule matches this alert; leaving it on screen for review: "
                + alert.describe(), alert=alert,
            )
        if rule.dismiss_with not in alert.buttons:
            raise UnexpectedAlertError(
                f"Policy rule {rule.name!r} expects button {rule.dismiss_with!r}, "
                f"but the alert offers {list(alert.buttons)}.", alert=alert,
            )
        started = self._clock()
        self._log.info("dismiss alert %s via %r", rule.name, rule.dismiss_with)
        self._backend.tap_alert_button(rule.dismiss_with)
        result = self._await_expectation(expect, timeout=timeout, what=f"alert {rule.name}")
        self.history.append(ActionRecord(
            verb="dismiss_alert", selector=rule.name, screen_id=snapshot.screen_id,
            token=snapshot.token().structure[:12], expectation=expect.describe(),
            elapsed=self._clock() - started,
        ))
        return result

    # --- internals ----------------------------------------------------------

    def _require_open(self) -> None:
        if self._closed:
            raise SessionClosedError("This UITestAdapter has been closed.")

    def _authorize_business(self, business_write: bool, what: str) -> None:
        self._require_open()
        if business_write and not self._target.allow_business_writes:
            raise BusinessActionNotAuthorizedError(
                f"Refusing {what}: it is flagged as a real business operation, and target "
                f"{self._target.describe()} has allow_business_writes=False."
            )

    def _capture(self) -> Snapshot:
        snapshot = retry_read(
            self._backend.capture, budget=self._budget, sleep=self._sleep,
            on_retry=lambda attempt, delay, error: self._log.warning(
                "Automation transport fault (attempt %d, backing off %.2fs): %s", attempt, delay, error),
        )
        if snapshot.bundle_id and snapshot.bundle_id != self._target.bundle_id:
            raise TargetNotAuthorizedError(
                f"Foreground app is {snapshot.bundle_id!r}, but the authorized target is "
                f"{self._target.bundle_id!r}. Stopping rather than driving an unknown app."
            )
        self._verify_container_stamp(snapshot)
        return snapshot

    def _verify_container_stamp(self, snapshot: Snapshot) -> None:
        """Cross-check the snapshot's container id against the bound container.

        Two independent sources must agree: what Crane reports as active (the
        container session) and what the backend stamped onto this snapshot. A
        disagreement means the dump cannot be trusted to belong to the container
        the test thinks it is driving, so the run stops.

        An empty stamp means the backend has no container provider wired up --
        not a mismatch, so it is allowed; the snapshot then simply carries no
        container evidence.
        """
        if self._container is None or not snapshot.container_id:
            return
        expected = getattr(self._container, "container_id", "")
        if expected and snapshot.container_id != expected:
            raise ContainerChangedError(
                f"Snapshot reports container {snapshot.container_id!r} but this session is "
                f"bound to {expected!r}. Stopping: the observed UI does not belong to the "
                "container under test."
            )

    def _assert_container(self) -> None:
        if self._container is not None:
            self._container.assert_stable()

    def _reject_unknown_alert(self, snapshot: Snapshot) -> None:
        alert = snapshot.alert
        if alert is None:
            return
        if self._alerts.classify(alert) is None:
            raise UnexpectedAlertError(
                "Stopping on an alert no policy rule recognises; it is left on screen for review: "
                + alert.describe(), alert=alert,
            )

    def _prepare(self, selector: Selector, *, recover: bool,
                 timeout: float | None = None) -> tuple[Snapshot, Element, str]:
        """Shared pre-action gate: container, alert, element readiness, animation."""
        self._require_open()
        self._assert_container()
        self._await_present(selector, timeout=timeout)
        settled = self._waiter.settle(description=selector.describe(),
                                      inspect=self._reject_unknown_alert)
        element = self._resolve(selector, settled)
        element, recovered = self._ensure_interactable(selector, element, settled, recover=recover)
        if recovered:
            settled = self._waiter.settle(description=selector.describe(),
                                          inspect=self._reject_unknown_alert)
            element = self._resolve(selector, settled)
        return settled, element, recovered

    def _await_present(self, selector: Selector, *, timeout: float | None,
                       interactable: bool = False) -> Snapshot:
        """Wait until the selector resolves to one ready element.

        Ambiguity fails fast rather than waiting: two elements sharing an
        identifier is a defect in the screen, not a transient state. On timeout
        the precise cause is re-derived so the caller sees
        :class:`ElementNotFoundError` or an interactability error instead of a
        bare timeout.
        """
        what = "interactable" if interactable else "visible"

        def ready(snapshot: Snapshot) -> bool:
            matches = selector.find_all(snapshot.elements())
            if selector.index is not None:
                if selector.index >= len(matches):
                    return False
                element = matches[selector.index]
            else:
                if len(matches) > 1:
                    raise AmbiguousSelectorError(
                        f"{selector.describe()} matched {len(matches)} elements on screen "
                        f"{snapshot.screen_id!r}: {[item.describe() for item in matches]}. "
                        "Give the element a unique accessibility identifier, or pass index= "
                        "for a genuinely repeated control."
                    )
                if not matches:
                    return False
                element = matches[0]
            if not element.visible:
                return False
            return not interactable or self._interactability_problem(element, snapshot) is None

        try:
            return self._waiter.poll(ready, description=f"{selector.describe()} to be {what}",
                                     timeout=timeout, inspect=self._reject_unknown_alert)
        except WaitTimeoutError:
            latest = self._capture()
            element = self._resolve(selector, latest)  # raises if absent/ambiguous
            problem = self._interactability_problem(element, latest) or "not_visible"
            raise self._interactability_error(selector, element, latest, problem) from None

    def _resolve(self, selector: Selector, snapshot: Snapshot) -> Element:
        matches = selector.find_all(snapshot.elements())
        if selector.index is not None:
            if selector.index >= len(matches):
                raise ElementNotFoundError(
                    f"{selector.describe()} wants index {selector.index} but only {len(matches)} "
                    f"element(s) match on screen {snapshot.screen_id!r}."
                )
            return matches[selector.index]
        if not matches:
            raise ElementNotFoundError(
                f"{selector.describe()} matched nothing on screen {snapshot.screen_id!r}. "
                f"Identifiers present: {self._identifiers(snapshot)}"
            )
        if len(matches) > 1:
            raise AmbiguousSelectorError(
                f"{selector.describe()} matched {len(matches)} elements on screen "
                f"{snapshot.screen_id!r}: {[element.describe() for element in matches]}. "
                "Give the element a unique accessibility identifier, or pass index= for a "
                "genuinely repeated control."
            )
        if selector.uses_fallback:
            self._log.info("%s resolved by a locale-dependent fallback; prefer an "
                           "accessibility identifier.", selector.describe())
        return matches[0]

    @staticmethod
    def _identifiers(snapshot: Snapshot) -> list[str]:
        found = [element.identifier for element in snapshot.elements() if element.identifier]
        return sorted(set(found))[:20]

    def _interactability_problem(self, element: Element, snapshot: Snapshot) -> str | None:
        if not element.enabled:
            return "disabled"
        if not element.visible:
            return "not_visible"
        if element.frame.empty:
            return "zero_sized"
        if not snapshot.window.contains(element.frame):
            return "outside_interactable_area"
        if snapshot.keyboard is not None and snapshot.keyboard.intersects(element.frame):
            return "keyboard_obstruction"
        if not element.hittable:
            return "not_hittable"
        return None

    def _ensure_interactable(self, selector: Selector, element: Element, snapshot: Snapshot,
                             *, recover: bool) -> tuple[Element, str]:
        problem = self._interactability_problem(element, snapshot)
        if problem is None:
            return element, ""
        if not recover:
            raise self._interactability_error(selector, element, snapshot, problem)
        recoverable = {"outside_interactable_area", "keyboard_obstruction", "not_hittable"}
        if problem not in recoverable:
            raise self._interactability_error(selector, element, snapshot, problem)

        # One bounded recovery attempt: adjust the *viewport*, never fake input.
        self._log.info("%s is %s; attempting one bounded recovery.", selector.describe(), problem)
        if problem == "keyboard_obstruction":
            self._backend.dismiss_keyboard()
        self._backend.scroll_into_view(element.ref)
        after = self._waiter.settle(description=f"{selector.describe()} after recovery",
                                    inspect=self._reject_unknown_alert)
        element = self._resolve(selector, after)
        problem_after = self._interactability_problem(element, after)
        if problem_after is not None:
            raise self._interactability_error(selector, element, after, problem_after,
                                              recovery_attempted=True)
        return element, problem

    def _interactability_error(self, selector: Selector, element: Element, snapshot: Snapshot,
                               problem: str, *, recovery_attempted: bool = False) -> UITestError:
        suffix = " after one recovery attempt" if recovery_attempted else ""
        detail = (f"{selector.describe()} -> {element.describe()} is not interactable{suffix}: "
                  f"{problem}. frame={element.frame}, window={snapshot.window}, "
                  f"keyboard={snapshot.keyboard}")
        if problem == "keyboard_obstruction":
            return KeyboardObstructionError(detail)
        return ElementNotInteractableError(detail, reason=problem)

    def _confirm_unchanged(self, selector: Selector, settled: Snapshot, *, recover: bool) -> Element:
        """Re-read immediately before dispatch, so a late transition cannot be
        tapped through."""
        confirm = self._capture()
        self._reject_unknown_alert(confirm)
        expected: ScreenToken = settled.token()
        actual: ScreenToken = confirm.token()
        if actual != expected:
            raise ScreenChangedError(
                f"Screen changed between the readiness check and the action: "
                f"expected {expected.describe()}, found {actual.describe()}. Nothing was tapped.",
                expected=expected, actual=actual,
            )
        element = self._resolve(selector, confirm)
        problem = self._interactability_problem(element, confirm)
        if problem is not None:
            raise self._interactability_error(selector, element, confirm, problem)
        return element

    def _guard_repeat(self, key: _ActionKey, *, allow_repeat: bool, what: str) -> None:
        if allow_repeat:
            return
        if key.as_tuple() in self._performed:
            raise RepeatedActionError(
                f"Refusing to repeat {what} on the same screen: this exact action already ran and "
                "the screen state is unchanged. If the control is genuinely repeatable, pass "
                "allow_repeat=True; otherwise the previous step did not take effect and the test "
                "should fail here rather than double-submit."
            )
        self._performed.add(key.as_tuple())

    def _await_expectation(self, expect: Expectation, *, timeout: float | None, what: str) -> Snapshot:
        description = expect.describe()
        try:
            self._waiter.poll(expect.satisfied, description=description, timeout=timeout,
                              inspect=self._reject_unknown_alert)
        except WaitTimeoutError as error:
            raise ExpectationFailedError(
                f"{what} dispatched, but its post-condition was never observed within "
                f"{error.elapsed:.2f}s: {description}.", description=description,
            ) from error
        settled = self._waiter.settle(description=description, inspect=self._reject_unknown_alert)
        if not expect.satisfied(settled):
            raise ExpectationFailedError(
                f"{what}: post-condition held transiently but not once the screen settled: "
                f"{description}.", description=description,
            )
        return settled

    def _screen_diff(self, spec: ScreenSpec, snapshot: Snapshot) -> tuple[tuple[Selector, ...], tuple[Selector, ...]]:
        elements = snapshot.elements()
        missing = tuple(selector for selector in spec.required if not selector.find_all(elements))
        unexpected = tuple(selector for selector in spec.forbidden if selector.find_all(elements))
        return missing, unexpected

    def _record(self, verb: str, selector: Selector, snapshot: Snapshot,
                expect: Expectation, started: float, recovered: str) -> None:
        self.history.append(ActionRecord(
            verb=verb, selector=selector.describe(), screen_id=snapshot.screen_id,
            token=snapshot.token().structure[:12], expectation=expect.describe(),
            elapsed=self._clock() - started, recovered=recovered,
            fallback_selector=selector.uses_fallback,
            container_id=snapshot.container_id,
        ))

    def _scrub(self, error: UITestError) -> UITestError:
        message = str(error)
        for secret in self._secrets:
            if secret:
                message = message.replace(secret, "[redacted]")
        if message == str(error):
            return error
        return type(error)(message)


def _digest(value: str) -> str:
    """Hash a typed value so the repeat guard can compare it without storing it."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
