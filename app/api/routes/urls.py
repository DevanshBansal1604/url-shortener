"""Translate HTTP requests and application outcomes at the API boundary."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from redis.asyncio import Redis

from app.api.dependencies import get_session, get_redis, limit_creation, limit_redirect
from app.core.config import settings
from app.schemas.url import URLCreate, URLCreated
from app.services.urls import (
    URLCreationError, URLLookupError, create_mapping, resolve_mapping,
)

router = APIRouter()
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
RedisDependency = Annotated[Redis, Depends(get_redis)]


@router.post("/urls", response_model=URLCreated, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(limit_creation)])
async def create_url(payload: URLCreate, session: SessionDependency) -> URLCreated:
    try:
        record = await create_mapping(session, str(payload.long_url))
    except URLCreationError:
        raise HTTPException(status_code=500, detail="Could not create short URL") from None
    return URLCreated(code=record.short_code, short_url=settings.short_url(record.short_code))


@router.get("/{code}", dependencies=[Depends(limit_redirect)])
async def redirect_url(
    code: str, session: SessionDependency, redis_client: RedisDependency,
) -> RedirectResponse:
    try:
        record = await resolve_mapping(session, code, redis_client)
    except URLLookupError:
        raise HTTPException(status_code=500, detail="Could not retrieve short URL") from None
    if record is None:
        raise HTTPException(status_code=404, detail="Short URL not found")
    return RedirectResponse(url=str(record.original_url), status_code=status.HTTP_302_FOUND)
