"""Real PostgreSQL tests with one rollback-controlled transaction per test."""

import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
DEVELOPMENT_DB = os.environ["POSTGRES_DB"]
TEST_DB = os.getenv("TEST_POSTGRES_DB", f"{DEVELOPMENT_DB}_test")
if TEST_DB == DEVELOPMENT_DB or not TEST_DB.endswith("_test"):
    raise RuntimeError("TEST_POSTGRES_DB must differ from POSTGRES_DB and end in _test")

# Select the test database BEFORE importing the application/database modules.
os.environ["POSTGRES_DB"] = TEST_DB
os.environ["PUBLIC_BASE_URL"] = "http://test"

from app.db.session import database_url  # noqa: E402
from app.main import app  # noqa: E402
from app.api.dependencies import get_session, get_redis  # noqa: E402


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def migrated_database():
    """Apply real migrations once; never create tables from ORM metadata."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    if result.returncode:
        pytest.fail(
            "Test database migration failed. Start PostgreSQL and run "
            "python scripts/prepare_test_database.py first.\n"
            + result.stderr,
            pytrace=False,
        )


@pytest.fixture
async def test_engine(migrated_database, anyio_backend):
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(test_engine):
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            async with AsyncSession(
                bind=connection,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            ) as session:
                yield session
        finally:
            await transaction.rollback()


@pytest.fixture
def redis_cache():
    cache = AsyncMock()
    pipe = AsyncMock()
    pipe.zincrby = MagicMock()
    pipe.expireat = MagicMock()
    pipe.__aenter__.return_value = pipe
    cache.pipeline = MagicMock(return_value=pipe)
    cache.eval.return_value = [1, 0]  # Limiter allows by default; dedicated tests cover denial.
    cache.get.return_value = None
    cache.zcard.return_value = 0
    cache.zrevrank.return_value = None
    return cache


@pytest.fixture
async def client(db_session, redis_cache):
    async def override_get_session():
        yield db_session

    sentinel = object()
    previous = app.dependency_overrides.get(get_session, sentinel)
    previous_redis = app.dependency_overrides.get(get_redis, sentinel)
    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_redis] = lambda: redis_cache
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as test_client:
            yield test_client
    finally:
        if previous is sentinel:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = previous
        if previous_redis is sentinel:
            app.dependency_overrides.pop(get_redis, None)
        else:
            app.dependency_overrides[get_redis] = previous_redis
