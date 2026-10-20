"""Timing policy: readiness, settling, bounded timeouts, infrastructure backoff.

Replaces fixed sleeps. Four distinct mechanisms, deliberately not interchangeable:

* :meth:`Waiter.poll`   - wait until a *UI predicate* holds (element ready, state reached).
* :meth:`Waiter.settle` - wait until the screen stops changing (animation/transition done).
* :class:`Deadline`     - every wait is bounded; expiry raises, it never proceeds anyway.
* :func:`retry_read`    - bounded exponential backoff, for read-only transport faults only.

What this module does not do: randomised pauses, jittered inter-action delays, or
motion/dwell patterns. Those exist only to shape how automation looks to an
observer, which is out of scope for a test harness.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Iterator, TypeVar

from .errors import InfrastructureError, NotSettledError, WaitTimeoutError
from .model import Snapshot

T = TypeVar("T")

Clock = Callable[[], float]
Sleep = Callable[[float], None]


@dataclass(frozen=True)
class Budget:
    """Bounded timing budget. Every field is a hard ceiling, not a target."""
    timeout: float = 10.0
    poll_interval: float = 0.25
    settle_for: float = 0.3
    settle_samples: int = 2
    settle_timeout: float = 5.0
    infra_retries: int = 3
    infra_backoff: float = 0.5
    infra_backoff_max: float = 4.0

    def __post_init__(self) -> None:
        for name in ("timeout", "poll_interval", "settle_for", "settle_timeout",
                     "infra_backoff", "infra_backoff_max"):
            if float(getattr(self, name)) <= 0:
                raise ValueError(f"Budget.{name} must be > 0.")
        if self.settle_samples < 2:
            raise ValueError("Budget.settle_samples must be >= 2 to observe stability.")
        if self.infra_retries < 0:
            raise ValueError("Budget.infra_retries must be >= 0.")

    def with_timeout(self, timeout: float | None) -> "Budget":
        if timeout is None:
            return self
        return Budget(**{**self.__dict__, "timeout": float(timeout)})


class Deadline:
    """A monotonic wall-clock ceiling."""

    def __init__(self, timeout: float, *, clock: Clock = time.monotonic) -> None:
        self._clock = clock
        self._start = clock()
        self._timeout = float(timeout)

    @property
    def elapsed(self) -> float:
        return self._clock() - self._start

    @property
    def remaining(self) -> float:
        return self._timeout - self.elapsed

    @property
    def expired(self) -> bool:
        return self.remaining <= 0


def backoff_delays(retries: int, base: float, cap: float) -> Iterator[float]:
    """Deterministic exponential backoff, capped.

    No jitter: this is a retry schedule for flaky transports, not traffic
    shaping meant to disguise automation.
    """
    delay = float(base)
    for _ in range(max(0, int(retries))):
        yield min(delay, float(cap))
        delay *= 2


def retry_read(call: Callable[[], T], *, budget: Budget, sleep: Sleep = time.sleep,
               on_retry: Callable[[int, float, InfrastructureError], None] | None = None) -> T:
    """Run a read-only call, retrying :class:`InfrastructureError` with backoff.

    Never use this for taps or text entry: a retried mutating call can submit a
    business operation twice.
    """
    attempt = 0
    last: InfrastructureError | None = None
    for delay in backoff_delays(budget.infra_retries, budget.infra_backoff, budget.infra_backoff_max):
        try:
            return call()
        except InfrastructureError as error:
            attempt += 1
            last = error
            if on_retry is not None:
                on_retry(attempt, delay, error)
            sleep(delay)
    try:
        return call()
    except InfrastructureError as error:
        raise InfrastructureError(
            f"Automation transport still failing after {budget.infra_retries} retries: {error}"
        ) from (last or error)


class Waiter:
    """Polls a capture callable until a condition holds, within a budget."""

    def __init__(self, capture: Callable[[], Snapshot], *, budget: Budget,
                 clock: Clock = time.monotonic, sleep: Sleep = time.sleep) -> None:
        self._capture = capture
        self._budget = budget
        self._clock = clock
        self._sleep = sleep

    @property
    def budget(self) -> Budget:
        return self._budget

    def poll(self, predicate: Callable[[Snapshot], bool], *, description: str,
             timeout: float | None = None,
             inspect: Callable[[Snapshot], None] | None = None) -> Snapshot:
        """Return the first snapshot satisfying ``predicate``.

        ``inspect`` runs on every sample before the predicate and may raise to
        abort early -- that is how an unrecognised alert stops a wait instead of
        burning the whole timeout.
        """
        budget = self._budget.with_timeout(timeout)
        deadline = Deadline(budget.timeout, clock=self._clock)
        snapshot: Snapshot | None = None
        while True:
            snapshot = self._capture()
            if inspect is not None:
                inspect(snapshot)
            if predicate(snapshot):
                return snapshot
            if deadline.expired:
                where = f" (screen {snapshot.screen_id})" if snapshot is not None else ""
                raise WaitTimeoutError(
                    f"Timed out after {deadline.elapsed:.2f}s waiting for {description}{where}",
                    description=description, elapsed=deadline.elapsed,
                )
            self._sleep(budget.poll_interval)

    def settle(self, *, description: str = "the screen to stop changing",
               timeout: float | None = None,
               inspect: Callable[[Snapshot], None] | None = None) -> Snapshot:
        """Wait until consecutive snapshots are identical for ``settle_for`` seconds.

        This is the animation gate: a transition, a keyboard slide-in or a
        spinner all keep the fingerprint moving, so the adapter holds off until
        the UI is genuinely still.
        """
        budget = self._budget
        limit = budget.settle_timeout if timeout is None else float(timeout)
        deadline = Deadline(limit, clock=self._clock)
        stable_since: float | None = None
        previous: str | None = None
        samples = 0
        while True:
            snapshot = self._capture()
            if inspect is not None:
                inspect(snapshot)
            current = snapshot.fingerprint()
            now = self._clock()
            if current == previous:
                samples += 1
                if stable_since is None:
                    stable_since = now
                if samples >= budget.settle_samples - 1 and (now - stable_since) >= budget.settle_for:
                    return snapshot
            else:
                previous = current
                stable_since = None
                samples = 0
            if deadline.expired:
                raise NotSettledError(
                    f"Screen still changing after {deadline.elapsed:.2f}s; refusing to act on {description}.",
                    description=description, elapsed=deadline.elapsed,
                )
            self._sleep(budget.poll_interval)
