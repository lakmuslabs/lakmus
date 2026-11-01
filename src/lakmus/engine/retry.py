"""How long to wait before trying a target again.

Kept pure and separate so that the policy can be tested without a clock, a target or a run: the
jitter arrives as a number, and the caller draws it from the run's seeded generator. That is what
makes the *sequence* of waits reproducible even though the timing never is.
"""

from dataclasses import dataclass

DEFAULT_ATTEMPTS = 5
DEFAULT_BASE_S = 1.0
DEFAULT_CAP_S = 60.0


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """One policy for every adapter, as ADR 0007 requires."""

    attempts: int = DEFAULT_ATTEMPTS
    base_s: float = DEFAULT_BASE_S
    cap_s: float = DEFAULT_CAP_S

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("a retry policy needs at least one attempt")
        if self.base_s <= 0 or self.cap_s < self.base_s:
            raise ValueError("a retry policy needs 0 < base <= cap")

    def wait_for(self, attempt: int, *, jitter: float, retry_after_s: float | None = None) -> float:
        """Seconds to wait after a failed attempt, counted from 1.

        `retry_after_s` replaces the computed wait rather than adding to it: the provider knows
        when its window opens, and guessing on top of that advice only wastes the window.
        """
        if attempt < 1:
            raise ValueError("attempts are counted from 1")
        if retry_after_s is not None:
            return max(0.0, retry_after_s)
        exponential = min(self.cap_s, self.base_s * 2.0 ** (attempt - 1))
        return exponential * (0.5 + jitter)
