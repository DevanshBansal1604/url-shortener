"""Exercise actual Redis ranking and window transitions with an isolated prefix."""
from unittest.mock import AsyncMock
from uuid import uuid4
import time

import pytest

from app.core.redis import create_redis_client
from app.services import popularity

pytestmark = pytest.mark.anyio


async def test_window_ranking_counts_ties_and_cleanup(monkeypatch):
    cache = create_redis_client()
    monkeypatch.setattr(popularity, "PREFIX", f"test:{uuid4().hex}")
    now = [time.time()]
    monkeypatch.setattr(popularity.time, "time", lambda: now[0])
    window = popularity.window_id()
    first = popularity.counts_key(window)
    second = popularity.counts_key(window + 1)
    try:
        assert not await popularity.is_eligible(cache, "A", window)
        for code, hits in zip("ABCDEFGHIJ", range(10, 0, -1)):
            for _ in range(hits):
                await popularity.record_access(cache, code)
        assert await cache.zcard(first) == 10
        assert await cache.zscore(first, "A") == 10
        assert 600 <= await cache.ttl(first) <= 1200
        now[0] += 600
        assert [c for c in "ABCDEFGHIJ" if await popularity.is_eligible(cache, c, window + 1)] == ["A", "B"]
        for _ in range(20):
            await popularity.record_access(cache, "C")
        assert not await popularity.is_eligible(cache, "C", window + 1)
        now[0] += 600
        assert await popularity.is_eligible(cache, "C", window + 2)
        assert not await popularity.is_eligible(cache, "A", window + 2)
        # Equal scores have a deterministic reverse-lexicographic tie break.
        await cache.zadd(second, {"D": 20, "E": 20, "F": 20, "G": 20})
        assert await popularity.is_eligible(cache, "G", window + 2)
        assert not await popularity.is_eligible(cache, "C", window + 2)
        assert popularity.destination_key(window, "A") != popularity.destination_key(window + 1, "A")
    finally:
        await cache.delete(first, second)
        await cache.aclose()
