"""Shared per-IP token buckets. Redis owns time and the atomic decision."""

from dataclasses import dataclass
import logging
from math import ceil

from redis.asyncio import Redis
from redis.exceptions import ConnectionError, TimeoutError

logger = logging.getLogger(__name__)
PREFIX = "rate_limit:v1"


@dataclass(frozen=True)
class Policy:
    operation: str
    capacity: int
    refill_per_minute: float


CREATE = Policy("create", 10, 10)
REDIRECT = Policy("redirect", 50, 50)

# Keep this short: Redis blocks other commands during script execution.
# Return milliseconds: Lua numeric replies otherwise lose fractional seconds.
SCRIPT = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local ttl_ms = tonumber(ARGV[3])
local time = redis.call('TIME')
local now = tonumber(time[1]) + tonumber(time[2]) / 1000000
local state = redis.call('HMGET', KEYS[1], 'tokens', 'last_updated')
local tokens = tonumber(state[1]) or capacity
local previous = tonumber(state[2]) or now
-- A wall-clock adjustment must not mint credit or move the baseline backwards.
now = math.max(now, previous)
tokens = math.min(capacity, tokens + (now - previous) * rate)
local allowed = 0
local retry_ms = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
else
    retry_ms = math.ceil((1 - tokens) / rate * 1000)
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'last_updated', tostring(now))
redis.call('PEXPIRE', KEYS[1], ttl_ms)
return {allowed, retry_ms}
"""


def bucket_key(ip: str, policy: Policy) -> str:
    return f"{PREFIX}:{policy.operation}:{ip}"


async def check_limit(redis: Redis, ip: str, policy: Policy) -> tuple[bool, int]:
    """Return (allowed, Retry-After seconds); connection outages fail open.

    Script/programming errors deliberately propagate rather than silently
    disabling enforcement. No retries: a timed-out script may have spent a token.
    """
    rate = policy.refill_per_minute / 60
    ttl_ms = ceil(policy.capacity / rate * 1000)
    try:
        allowed, retry_ms = await redis.eval(
            SCRIPT, 1, bucket_key(ip, policy), policy.capacity, rate, ttl_ms,
        )
    except (ConnectionError, TimeoutError):
        logger.warning("Rate limiter unavailable; allowing %s request", policy.operation)
        return True, 0
    return bool(allowed), ceil(int(retry_ms) / 1000)
