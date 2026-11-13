"""Build a shared async Redis client; the application lifespan owns cleanup."""

from redis.asyncio import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.core.config import settings


def create_redis_client() -> Redis:
    return Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
        retry=Retry(NoBackoff(), 0),
    )
