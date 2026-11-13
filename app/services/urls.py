"""URL creation and lookup operations, independent of HTTP routing."""

import logging

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services import popularity
from app.models.url import URL, url_id_sequence
from app.utils.base62 import encode_base62
from app.schemas.url import URLMapping

CACHE_TTL_SECONDS = 300

logger = logging.getLogger(__name__)


class URLCreationError(Exception):
    """Creating a mapping failed; the transaction has been rolled back."""


class URLLookupError(Exception):
    """The mapping could not be retrieved or validated."""


async def _rollback_failed_creation(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except SQLAlchemyError:
        logger.exception("Could not roll back failed URL creation")


async def create_mapping(session: AsyncSession, original_url: str) -> URL:
    """Commit a new mapping; repeated URLs intentionally get distinct codes."""
    try:
        url_id = await session.scalar(url_id_sequence.next_value())
        code = encode_base62(url_id)
        # Check the generated public URL before committing, as before refactoring.
        settings.short_url(code)
        record = URL(id=url_id, original_url=original_url, short_code=code)
        session.add(record)
        await session.commit()
    except Exception as exc:
        logger.exception("Could not save URL mapping")
        await _rollback_failed_creation(session)
        raise URLCreationError("Could not create short URL") from exc
    return record


async def resolve_mapping(
    session: AsyncSession, code: str, redis_client: Redis,
) -> URLMapping | None:
    """Cache only previous-window winners; Redis failures never block the DB."""
    window = popularity.window_id()
    key = popularity.destination_key(window, code)
    eligible = False
    mapping = None
    try:
        eligible = await popularity.is_eligible(redis_client, code, window)
        if eligible:
            cached = await redis_client.get(key)
            if cached is not None:
                try:
                    mapping = URLMapping.model_validate_json(cached)
                    if mapping.short_code != code:
                        mapping = None
                except ValidationError:
                    logger.warning("Ignoring invalid cached mapping")
    except RedisError:
        logger.warning("Redis lookup unavailable; using PostgreSQL")

    if mapping is None:
        try:
            result = await session.execute(select(URL).where(URL.short_code == code))
            record = result.scalar_one_or_none()
            if record is None:
                return None
            mapping = URLMapping.model_validate(record)
        except (SQLAlchemyError, ValidationError) as exc:
            logger.exception("Could not look up short URL")
            raise URLLookupError("Could not retrieve short URL") from exc
        if eligible:
            try:
                await redis_client.set(key, mapping.model_dump_json(), ex=CACHE_TTL_SECONDS)
            except RedisError:
                logger.warning("Redis cache write unavailable")

    try:
        await popularity.record_access(redis_client, code)
    except RedisError:
        logger.warning("Redis access counter unavailable")
    return mapping
