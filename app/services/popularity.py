"""Fixed-window popularity; previous-window scores decide cache admission."""

import time
from redis.asyncio import Redis

WINDOW_SECONDS = 600
PREFIX = "popular:v1"


def window_id() -> int:
    return int(time.time() // WINDOW_SECONDS)


def counts_key(window: int) -> str:
    return f"{PREFIX}:counts:{window}"


def destination_key(window: int, code: str) -> str:
    # Window-scoped destinations cannot serve a previous window's losers.
    return f"{PREFIX}:dest:{window}:{code}"


async def is_eligible(client: Redis, code: str, window: int) -> bool:
    key = counts_key(window - 1)
    size = await client.zcard(key)
    if not size:
        return False
    rank = await client.zrevrank(key, code)
    # Integer ceiling of 20%; Redis breaks equal scores by reverse code order.
    return rank is not None and rank < (size + 4) // 5


async def record_access(client: Redis, code: str) -> None:
    # Count at successful resolution time, so completed windows stop changing.
    window = window_id()
    key = counts_key(window)
    async with client.pipeline(transaction=True) as pipe:
        pipe.zincrby(key, 1, code)
        # Keep the entire next window; expire counters at the following boundary.
        pipe.expireat(key, (window + 2) * WINDOW_SECONDS)
        await pipe.execute()
