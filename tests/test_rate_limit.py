"""Real Redis atomicity plus HTTP integration with isolated, disposable keys."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError, ResponseError, TimeoutError

from app.api.dependencies import get_redis, get_session
from app.api.routes import urls
from app.core.redis import create_redis_client
from app.schemas.url import URLMapping
from app.services import rate_limit as limiter

pytestmark = pytest.mark.anyio


@pytest.fixture
async def real_redis(monkeypatch):
    monkeypatch.setattr(limiter, 'PREFIX', f'test:limiter:{uuid4().hex}')
    first, second = create_redis_client(), create_redis_client()
    try:
        await first.ping()
        yield first, second
    finally:
        try:
            keys = [key async for key in first.scan_iter(match=f'{limiter.PREFIX}:*')]
            if keys:
                await first.delete(*keys)
        finally:
            await first.aclose()
            await second.aclose()


async def test_burst_independent_ips_and_operations(real_redis):
    redis, _ = real_redis
    for _ in range(10):
        assert (await limiter.check_limit(redis, 'a', limiter.CREATE))[0]
    assert await limiter.check_limit(redis, 'a', limiter.CREATE) == (False, 6)
    assert (await limiter.check_limit(redis, 'b', limiter.CREATE))[0]
    for _ in range(50):
        assert (await limiter.check_limit(redis, 'a', limiter.REDIRECT))[0]
    assert await limiter.check_limit(redis, 'a', limiter.REDIRECT) == (False, 2)
    assert 0 < await redis.pttl(limiter.bucket_key('a', limiter.CREATE)) <= 60000


async def test_fractional_refill_and_capacity(real_redis):
    redis, _ = real_redis
    key = limiter.bucket_key('a', limiter.CREATE)
    seconds, micros = await redis.time()
    now = seconds + micros / 1_000_000
    await redis.hset(key, mapping={'tokens': 0, 'last_updated': now - 3})
    assert await limiter.check_limit(redis, 'a', limiter.CREATE) == (False, 3)
    assert 0.5 <= float(await redis.hget(key, 'tokens')) < 0.6
    assert float(await redis.hget(key, 'last_updated')) >= now
    await redis.hset(key, mapping={'tokens': 0, 'last_updated': now - 12})
    assert (await limiter.check_limit(redis, 'a', limiter.CREATE))[0]
    assert 1 <= float(await redis.hget(key, 'tokens')) < 1.1
    await redis.hset(key, mapping={'tokens': 0, 'last_updated': now - 120})
    assert (await limiter.check_limit(redis, 'a', limiter.CREATE))[0]
    assert float(await redis.hget(key, 'tokens')) == 9


async def test_concurrent_clients_cannot_spend_same_token(real_redis):
    first, second = real_redis
    # Negligible refill keeps the race independent of machine speed.
    policy = limiter.Policy('race', 1, 0.000001)
    results = await asyncio.gather(*[
        limiter.check_limit(first if i % 2 else second, 'same-ip', policy)
        for i in range(40)
    ])
    assert sum(allowed for allowed, _ in results) == 1


async def test_expired_idle_bucket_restarts_full(real_redis):
    redis, _ = real_redis
    key = limiter.bucket_key('idle', limiter.CREATE)
    for _ in range(10):
        await limiter.check_limit(redis, 'idle', limiter.CREATE)
    # Represent a bucket that already earned a full refill before expiring it.
    sec, micro = await redis.time()
    await redis.hset(key, 'last_updated', sec + micro / 1_000_000 - 61)
    await redis.pexpire(key, 1)
    for _ in range(100):
        if not await redis.exists(key):
            break
        await asyncio.sleep(0.01)
    assert not await redis.exists(key)
    assert (await limiter.check_limit(redis, 'idle', limiter.CREATE))[0]
    assert float(await redis.hget(key, 'tokens')) == 9


@pytest.mark.parametrize('error', [ConnectionError, TimeoutError])
async def test_outage_fails_open(error, caplog):
    redis = AsyncMock()
    redis.eval.side_effect = error('offline')
    assert await limiter.check_limit(redis, 'a', limiter.CREATE) == (True, 0)
    assert 'allowing create request' in caplog.text


async def test_script_errors_are_not_hidden():
    redis = AsyncMock()
    redis.eval.side_effect = ResponseError('broken script')
    with pytest.raises(ResponseError):
        await limiter.check_limit(redis, 'a', limiter.CREATE)


@pytest.fixture
async def http_app(monkeypatch):
    app = FastAPI()
    app.include_router(urls.router)
    async def session():
        yield AsyncMock()
    app.dependency_overrides[get_session] = session
    mapping = URLMapping(original_url='https://example.com/', short_code='abc')
    from types import SimpleNamespace
    create = AsyncMock(return_value=SimpleNamespace(short_code='abc'))
    resolve = AsyncMock(return_value=mapping)
    monkeypatch.setattr(urls, 'create_mapping', create)
    monkeypatch.setattr(urls, 'resolve_mapping', resolve)
    yield app, create, resolve


async def test_http_separate_limits_and_worker_instances(real_redis, http_app):
    first, second = real_redis
    app, create, resolve = http_app
    app.dependency_overrides[get_redis] = lambda: first
    # A second app instance represents another worker sharing Redis.
    other = FastAPI()
    other.include_router(urls.router)
    other.dependency_overrides.update(app.dependency_overrides)
    other.dependency_overrides[get_redis] = lambda: second
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as a, AsyncClient(transport=ASGITransport(app=other), base_url='http://test') as b:
        for i in range(10):
            response = await (a if i % 2 else b).post('/urls', json={'long_url': 'https://example.com/'})
            assert response.status_code == 201
        rejected = await a.post('/urls', json={'long_url': 'https://example.com/'})
        assert rejected.status_code == 429
        assert rejected.headers['retry-after'] == '6'
        assert create.await_count == 10
        for i in range(50):
            response = await (a if i % 2 else b).get('/abc')
            assert response.status_code == 302
        rejected = await b.get('/different-code')
        assert rejected.status_code == 429
        assert rejected.headers['retry-after'] == '2'
        assert resolve.await_count == 50
    async with AsyncClient(transport=ASGITransport(app=app, client=('203.0.113.9', 123)), base_url='http://test') as new_ip:
        assert (await new_ip.post('/urls', json={'long_url': 'https://example.com/'})).status_code == 201


async def test_http_outage_allows_normal_behavior(http_app):
    app, create, resolve = http_app
    redis = AsyncMock()
    redis.eval.side_effect = ConnectionError('offline')
    app.dependency_overrides[get_redis] = lambda: redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        for _ in range(11):
            assert (await client.post('/urls', json={'long_url': 'https://example.com/'})).status_code == 201
        resolve.return_value = None
        assert (await client.get('/unknown')).status_code == 404
    assert create.await_count == 11
