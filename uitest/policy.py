"""Authorization and alert policy.

Two gates sit in front of every action:

* :class:`AuthorizedTarget` -- the adapter runs only against an app that has been
  explicitly declared as a licensed test/demo build, with a recorded
  authorization reference and owner. There is no implicit default target, and a
  target whose environment is ``production`` is rejected outright.
* :class:`AlertPolicy` -- only alerts declared ahead of time may be dismissed,
  and only via the button the rule names. Anything else halts the run with the
  alert left on screen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

from .errors import TargetNotAuthorizedError
from .model import Alert

TEST_ENVIRONMENTS = frozenset({"test", "demo", "staging", "sandbox", "local"})


@dataclass(frozen=True)
class AuthorizedTarget:
    """A declared, licensed app under test.

    ``allow_business_writes`` must be turned on explicitly, per target, for a
    test that completes a real business operation (a transfer, an order, a
    reward claim). It stays False for read/navigation suites, and the adapter
    refuses any action flagged ``business_write`` while it is False.
    """
    bundle_id: str
    environment: str
    authorization_ref: str
    owner: str
    allow_business_writes: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.bundle_id:
            raise ValueError("AuthorizedTarget.bundle_id is required.")
        environment = self.environment.strip().lower()
        if environment not in TEST_ENVIRONMENTS:
            raise TargetNotAuthorizedError(
                f"{self.bundle_id}: environment {self.environment!r} is not a test environment "
                f"(allowed: {sorted(TEST_ENVIRONMENTS)}). Production targets are not supported."
            )
        if not self.authorization_ref.strip():
            raise TargetNotAuthorizedError(
                f"{self.bundle_id}: authorization_ref is required (ticket/contract that permits this testing)."
            )
        if not self.owner.strip():
            raise TargetNotAuthorizedError(f"{self.bundle_id}: owner is required.")
        object.__setattr__(self, "environment", environment)

    def describe(self) -> str:
        return f"{self.bundle_id} ({self.environment}, auth {self.authorization_ref})"


@dataclass(frozen=True)
class TargetRegistry:
    """The declared allow-list. Empty by default: nothing is implicitly in scope."""
    targets: tuple[AuthorizedTarget, ...] = ()

    @classmethod
    def from_file(cls, path: str | Path) -> "TargetRegistry":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = raw.get("targets", raw) if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            raise ValueError("Authorized targets file must hold a list, or an object with a 'targets' list.")
        return cls(tuple(AuthorizedTarget(**entry) for entry in entries))

    def require(self, bundle_id: str) -> AuthorizedTarget:
        for target in self.targets:
            if target.bundle_id == bundle_id:
                return target
        known = ", ".join(target.bundle_id for target in self.targets) or "<none declared>"
        raise TargetNotAuthorizedError(
            f"{bundle_id!r} is not a declared test target. Declared: {known}. "
            "Add it to the authorized-targets file only with a licensed test build and a written authorization."
        )


@dataclass(frozen=True)
class AlertRule:
    """A pre-declared alert and the single button the test may use on it.

    ``identifier`` is matched first; ``message`` and ``title`` are exact-match
    fallbacks for alerts that ship without one. ``dismiss_with`` is the exact
    button label the test framework taps -- the adapter never reaches into the
    alert's handler block or any other private dismissal path.
    """
    name: str
    dismiss_with: str
    identifier: str = ""
    title: str = ""
    message: str = ""
    system: bool = False

    def __post_init__(self) -> None:
        if not (self.identifier or self.title or self.message):
            raise ValueError(f"AlertRule {self.name!r} needs an identifier, title or message to match on.")
        if not self.dismiss_with:
            raise ValueError(f"AlertRule {self.name!r} needs the exact button label to tap.")

    def matches(self, alert: Alert) -> bool:
        if self.system is not alert.system:
            return False
        if self.identifier:
            return alert.identifier == self.identifier
        if self.title and alert.title != self.title:
            return False
        if self.message and alert.message != self.message:
            return False
        return bool(self.title or self.message)


@dataclass(frozen=True)
class AlertPolicy:
    """Closed-world alert handling: declared alerts only."""
    rules: tuple[AlertRule, ...] = field(default=())

    def classify(self, alert: Alert) -> AlertRule | None:
        """Return the single matching rule, or None if the alert is unrecognised.

        Two matching rules is also "unrecognised": an ambiguous policy must not
        silently pick one.
        """
        matches = [rule for rule in self.rules if rule.matches(alert)]
        return matches[0] if len(matches) == 1 else None

    def with_rules(self, *rules: AlertRule) -> "AlertPolicy":
        return AlertPolicy(self.rules + tuple(rules))
