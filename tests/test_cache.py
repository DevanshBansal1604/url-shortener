"""Cache-aside contracts and real Redis expiry with isolated test DB rows."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.api.dependencies import get_redis
from app.core.redis import create_redis_client
from app.main import app
from app.schemas.url import URLMapping
from app.services import urls, popularity, rate_limit
from redis.exceptions import ConnectionError as RedisConnectionError
from uuid import uuid4

pytestmark = pytest.mark.anyio
DESTINATION = "https://example.com/articles/cache"


async def test_cache_hit_skips_database(client, db_session, redis_cache, monkeypatch):
    mapping = URLMapping(original_url=DESTINATION, short_code="abc")
    redis_cache.zcard.return_value = 5
    redis_cache.zrevrank.return_value = 0
    redis_cache.get.return_value = mapping.model_dump_json()
    execute = AsyncMock(side_effect=AssertionError("Cache hit queried database"))
    monkeypatch.setattr(db_session, "execute", execute)
    response = await client.get("/abc")
    assert response.status_code == 302
    assert response.headers["location"] == DESTINATION
    execute.assert_not_awaited()
    redis_cache.set.assert_not_awaited()  # Hits do not refresh TTL.
    redis_cache.pipeline.return_value.zincrby.assert_called_once()


async def test_unknown_code_not_cached(client, redis_cache):
    response = await client.get("/this_code_cannot_exist")
    assert response.status_code == 404
    redis_cache.set.assert_not_awaited()


async def test_database_error_not_cached(client, db_session, redis_cache, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    monkeypatch.setattr(db_session, "execute", AsyncMock(side_effect=SQLAlchemyError()))
    response = await client.get("/abc")
    assert response.status_code == 500
    redis_cache.set.assert_not_awaited()


@pytest.mark.parametrize("payload", [
    "not json",
    '{"original_url":"not-a-url","short_code":"abc"}',
    '{"original_url":"https://example.com/","short_code":"wrong"}',
])
async def test_invalid_cached_mapping_falls_back_to_database(client, redis_cache, payload):
    redis_cache.zcard.return_value = 5
    redis_cache.zrevrank.return_value = 0
    redis_cache.get.return_value = payload
    response = await client.get("/abc")
    assert response.status_code == 404


async def test_real_redis_miss_hit_and_expiry(client, db_session, monkeypatch):
    # This test requires the configured local Redis. It deletes only its own key.
    created = await client.post("/urls", json={"long_url": DESTINATION})
    assert created.status_code == 201
    code = created.json()["code"]
    monkeypatch.setattr(popularity, "PREFIX", f"test:{uuid4().hex}")
    monkeypatch.setattr(rate_limit, "PREFIX", f"test:limiter:{uuid4().hex}")
    window = popularity.window_id()
    key = popularity.destination_key(window, code)
    ranking = popularity.counts_key(window - 1)
    counts = popularity.counts_key(window)
    cache = create_redis_client()
    previous = app.dependency_overrides[get_redis]
    app.dependency_overrides[get_redis] = lambda: cache
    monkeypatch.setattr(urls, "CACHE_TTL_SECONDS", 1)
    execute = AsyncMock(wraps=db_session.execute)
    monkeypatch.setattr(db_session, "execute", execute)
    try:
        await cache.zadd(ranking, {code: 10})
        assert await cache.get(key) is None
        first = await client.get(f"/{code}")
        assert first.status_code == 302
        assert execute.await_count == 1
        cached = await cache.get(key)
        mapping = URLMapping.model_validate_json(cached)
        assert mapping.short_code == code
        assert str(mapping.original_url) == DESTINATION
        assert 0 <= await cache.ttl(key) <= 1
        from_database = await urls.resolve_mapping(db_session, code, cache)
        assert from_database == mapping
        second = await client.get(f"/{code}")
        assert second.status_code == 302
        assert second.headers["location"] == first.headers["location"] == DESTINATION
        assert execute.await_count == 1
        # Poll expiry with a bound rather than relying on an exact sleep boundary.
        for _ in range(40):
            if await cache.get(key) is None:
                break
            await asyncio.sleep(0.05)
        assert await cache.get(key) is None
        third = await client.get(f"/{code}")
        assert third.status_code == 302
        assert execute.await_count == 2
        assert URLMapping.model_validate_json(await cache.get(key)) == mapping
    finally:
        app.dependency_overrides[get_redis] = previous
        try:
            await cache.delete(key, ranking, counts, rate_limit.bucket_key("127.0.0.1", rate_limit.REDIRECT))
        finally:
            await cache.aclose()


@pytest.mark.parametrize("operation", ["zcard", "get", "set"])
async def test_redis_failure_still_redirects(client, redis_cache, operation):
    created = await client.post("/urls", json={"long_url": DESTINATION})
    code = created.json()["code"]
    redis_cache.zcard.return_value = 5
    redis_cache.zrevrank.return_value = 0
    getattr(redis_cache, operation).side_effect = RedisConnectionError("offline")
    response = await client.get(f"/{code}")
    assert response.status_code == 302
    assert response.headers["location"] == DESTINATION


async def test_counter_failure_still_redirects(client, monkeypatch):
    created = await client.post("/urls", json={"long_url": DESTINATION})
    monkeypatch.setattr(popularity, "record_access", AsyncMock(side_effect=RedisConnectionError()))
    response = await client.get(f"/{created.json()['code']}")
    assert response.status_code == 302


async def test_unknown_code_during_outage(client, redis_cache):
    redis_cache.zcard.side_effect = RedisConnectionError()
    response = await client.get("/unknown")
    assert response.status_code == 404


async def test_nonpopular_link_never_cached(client, redis_cache):
    created = await client.post("/urls", json={"long_url": DESTINATION})
    code = created.json()["code"]
    for _ in range(2):
        assert (await client.get(f"/{code}")).status_code == 302
    redis_cache.get.assert_not_awaited()
    redis_cache.set.assert_not_awaited()


async def test_real_unreachable_redis_redirect_and_recovery(client):
    from redis.asyncio import Redis
    from redis.retry import Retry
    from redis.backoff import NoBackoff
    created = await client.post("/urls", json={"long_url": DESTINATION})
    code = created.json()["code"]
    cache = Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.05,
                  socket_timeout=0.05, retry=Retry(NoBackoff(), 0))
    previous = app.dependency_overrides[get_redis]
    app.dependency_overrides[get_redis] = lambda: cache
    try:
        response = await client.get(f"/{code}")
        assert response.status_code == 302
        assert response.headers["location"] == DESTINATION
        assert (await client.get("/unknown")).status_code == 404
    finally:
        app.dependency_overrides[get_redis] = previous
        await cache.aclose()
    assert (await client.get(f"/{code}")).status_code == 302


async def test_stale_destination_and_explicit_invalidation(client, db_session, monkeypatch):
    from sqlalchemy import update
    from app.models.url import URL
    created = await client.post("/urls", json={"long_url": DESTINATION})
    code = created.json()["code"]
    monkeypatch.setattr(popularity, "PREFIX", f"test:{uuid4().hex}")
    monkeypatch.setattr(rate_limit, "PREFIX", f"test:limiter:{uuid4().hex}")
    window = popularity.window_id()
    monkeypatch.setattr(popularity, "window_id", lambda: window)
    key = popularity.destination_key(window, code)
    ranking = popularity.counts_key(window - 1)
    counts = popularity.counts_key(window)
    cache = create_redis_client()
    previous = app.dependency_overrides[get_redis]
    app.dependency_overrides[get_redis] = lambda: cache
    updated = "https://example.com/updated"
    try:
        await cache.zadd(ranking, {code: 1})
        assert (await client.get(f"/{code}")).headers["location"] == DESTINATION
        await db_session.execute(update(URL).where(URL.short_code == code).values(original_url=updated))
        assert (await client.get(f"/{code}")).headers["location"] == DESTINATION
        await cache.delete(key)
        assert (await client.get(f"/{code}")).headers["location"] == updated
        assert await cache.zscore(counts, code) == 3  # Includes the cache hit.
        # At the next boundary this code loses eligibility; old cache is skipped.
        await cache.zadd(counts, {"winner": 100})
        monkeypatch.setattr(popularity, "window_id", lambda: window + 1)
        assert (await client.get(f"/{code}")).headers["location"] == updated
        assert await cache.get(popularity.destination_key(window + 1, code)) is None
    finally:
        app.dependency_overrides[get_redis] = previous
        await cache.delete(key, ranking, counts, popularity.counts_key(window + 1),
                           rate_limit.bucket_key("127.0.0.1", rate_limit.REDIRECT))
        await cache.aclose()


async def test_query_reduction_for_eligible_link(client, db_session, monkeypatch):
    """Compare actual mapping SELECTs with forced misses and a real Redis cache."""
    from sqlalchemy import event

    created = await client.post("/urls", json={"long_url": DESTINATION})
    assert created.status_code == 201
    code = created.json()["code"]
    monkeypatch.setattr(popularity, "PREFIX", f"test:comparison:{uuid4().hex}")
    monkeypatch.setattr(rate_limit, "PREFIX", f"test:comparison:limiter:{uuid4().hex}")
    window = popularity.window_id()
    monkeypatch.setattr(popularity, "window_id", lambda: window)
    key = popularity.destination_key(window, code)
    ranking = popularity.counts_key(window - 1)
    counts = popularity.counts_key(window)
    cache = create_redis_client()
    previous = app.dependency_overrides[get_redis]
    app.dependency_overrides[get_redis] = lambda: cache
    engine = db_session.bind.sync_engine
    lookups = []

    def record_lookup(connection, cursor, statement, parameters, context, executemany):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("select ") and " from urls " in normalized:
            lookups.append(statement)

    event.listen(engine, "before_cursor_execute", record_lookup)
    try:
        await cache.zadd(ranking, {code: 100})
        assert await popularity.is_eligible(cache, code, window)
        assert await cache.get(key) is None

        async def redirect():
            response = await client.get(f"/{code}")
            assert response.status_code == 302
            assert response.headers["location"] == DESTINATION

        # Controlled baseline: force a destination miss before every request.
        for _ in range(20):
            await cache.delete(key)
            await redirect()
        baseline = len(lookups)
        assert baseline == 20

        await cache.delete(key)
        lookups.clear()
        for _ in range(20):
            await redirect()
        cached = len(lookups)
        assert cached == 1
        assert await cache.get(key) is not None
        assert await cache.ttl(key) > 0

        # Forty requests so far; these five remain within the 50-token burst.
        lookups.clear()
        for _ in range(5):
            await redirect()
        assert len(lookups) == 0
        assert await cache.zscore(counts, code) == 45
        reduction = (baseline - cached) / baseline * 100
        assert reduction == 95.0
        print(f"Mapping SELECTs: forced misses={baseline}/20, "
              f"cold cache={cached}/20, warm cache=0/5; reduction={reduction:.0f}%")
    finally:
        event.remove(engine, "before_cursor_execute", record_lookup)
        app.dependency_overrides[get_redis] = previous
        try:
            await cache.delete(key, ranking, counts,
                               rate_limit.bucket_key("127.0.0.1", rate_limit.REDIRECT))
        finally:
            await cache.aclose()
