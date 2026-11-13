"""Assemble the API and manage application-wide resources."""

from contextlib import asynccontextmanager
import logging

from redis.exceptions import RedisError

from fastapi import FastAPI

from app.api.routes.urls import router
from app.core.redis import create_redis_client
from app.db.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis_client = create_redis_client()
    try:
        try:
            await redis_client.ping()
        except RedisError:
            logging.getLogger(__name__).warning(
                "Redis unavailable at startup; cache fallback and rate-limit fail-open are active"
            )
        app.state.redis = redis_client
        yield
    finally:
        try:
            await redis_client.aclose()
        finally:
            await engine.dispose()


app = FastAPI(title="URL Shortener", lifespan=lifespan)
app.include_router(router)
