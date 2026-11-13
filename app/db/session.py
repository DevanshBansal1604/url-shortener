"""Shared async engine and session factory; importing does not connect."""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings

database_url = settings.database_url
engine = create_async_engine(database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
