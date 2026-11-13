"""Single-process token bucket; shared per-IP enforcement follows on Day 5."""

from collections.abc import Callable
from time import monotonic

CAPACITY = 50
REFILL_PER_SECOND = 50 / 60


class TokenBucket:
    """Start full, retain fractional credits, and spend one token per request.

    The clock must be monotonic and return seconds. Tests can supply a fake
    clock. Instances are local state, not a distributed rate limiter.
    """

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self.tokens = float(CAPACITY)
        self.last_updated = clock()

    def allow(self) -> bool:
        now = self._clock()
        elapsed = now - self.last_updated
        if elapsed < 0:
            raise ValueError("Token bucket clock must not move backwards")
        self.tokens = min(CAPACITY, self.tokens + elapsed * REFILL_PER_SECOND)
        # Save both values even on denial, so refill time is counted once.
        self.last_updated = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False
