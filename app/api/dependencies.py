"""Request-scoped dependencies shared by API routes."""

from collections.abc import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis

from app.db.session import SessionFactory
from app.services.rate_limit import CREATE, REDIRECT, Policy, check_limit


async def get_session() -> AsyncIterator[AsyncSession]:
    """Close the session after use; services own commit and rollback."""
    async with SessionFactory() as session:
        yield session


def get_redis(request: Request) -> Redis:
    """Reuse the client created and closed by application lifespan."""
    return request.app.state.redis



def rate_limit_dependency(policy: Policy):
    async def enforce(request: Request, redis: Redis = Depends(get_redis)) -> None:
        # Uvicorn resolves forwarded addresses only from configured trusted proxies.
        # Do not read X-Forwarded-For directly here.
        if request.client is None:
            raise HTTPException(status_code=503, detail="Client address unavailable")
        allowed, retry_after = await check_limit(redis, request.client.host, policy)
        if not allowed:
            raise HTTPException(
                status_code=429, detail="Rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )
    return enforce


limit_creation = rate_limit_dependency(CREATE)
limit_redirect = rate_limit_dependency(REDIRECT)
