"""Exercise real API routes and PostgreSQL; inject failures only where needed."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.models.url import URL, url_id_sequence

pytestmark = pytest.mark.anyio
ORIGINAL_URL = "https://example.com/articles/system-design"


async def row_count(session):
    return await session.scalar(select(func.count()).select_from(URL))


async def test_create_url_saves_mapping(client, db_session):
    before = await row_count(db_session)
    response = await client.post("/urls", json={"long_url": ORIGINAL_URL})
    assert response.status_code == 201
    body = response.json()
    assert body["code"]
    assert body["short_url"] == f"http://test/{body['code']}"
    saved = await db_session.scalar(select(URL).where(URL.short_code == body["code"]))
    assert saved is not None
    assert saved.original_url == ORIGINAL_URL
    assert saved.created_at is not None
    assert await row_count(db_session) == before + 1


async def test_created_url_redirects(client):
    created = await client.post("/urls", json={"long_url": ORIGINAL_URL})
    assert created.status_code == 201
    response = await client.get(f"/{created.json()['code']}")
    assert response.status_code == 302
    assert response.headers["location"] == ORIGINAL_URL


async def test_unknown_code_returns_404(client):
    # Longer than the model's maximum code length: cannot match a saved code.
    response = await client.get("/this_code_cannot_exist")
    assert response.status_code == 404
    assert response.json() == {"detail": "Short URL not found"}


@pytest.mark.parametrize("payload", [
    {},
    {"long_url": "not-a-url"},
    {"long_url": "ftp://example.com/file"},
    {"long_url": None},
    {"long_url": "https://example.com", "unexpected": True},
])
async def test_invalid_payload_inserts_nothing(client, db_session, payload):
    before = await row_count(db_session)
    response = await client.post("/urls", json=payload)
    assert response.status_code == 422
    assert await row_count(db_session) == before


async def test_repeated_url_creates_independent_links(client, db_session):
    before = await row_count(db_session)
    first = await client.post("/urls", json={"long_url": ORIGINAL_URL})
    second = await client.post("/urls", json={"long_url": ORIGINAL_URL})
    assert first.status_code == second.status_code == 201
    assert first.json()["code"] != second.json()["code"]
    assert await row_count(db_session) == before + 2


async def test_commit_failure_returns_500_and_rolls_back(client, db_session, monkeypatch):
    before = await row_count(db_session)
    failing_commit = AsyncMock(side_effect=SQLAlchemyError("Simulated commit failure"))
    rollback_spy = AsyncMock(wraps=db_session.rollback)
    monkeypatch.setattr(db_session, "commit", failing_commit)
    monkeypatch.setattr(db_session, "rollback", rollback_spy)
    response = await client.post("/urls", json={"long_url": ORIGINAL_URL})
    assert response.status_code == 500
    assert response.json() == {"detail": "Could not create short URL"}
    failing_commit.assert_awaited_once()
    rollback_spy.assert_awaited_once()
    assert await row_count(db_session) == before


async def test_lookup_failure_returns_generic_500(client, db_session, monkeypatch):
    monkeypatch.setattr(
        db_session, "execute",
        AsyncMock(side_effect=SQLAlchemyError("Private database details")),
    )
    response = await client.get("/some-code")
    assert response.status_code == 500
    assert response.json() == {"detail": "Could not retrieve short URL"}


async def test_database_rejects_duplicate_code_and_session_recovers(client, db_session):
    created = await client.post("/urls", json={"long_url": ORIGINAL_URL})
    assert created.status_code == 201
    code = created.json()["code"]
    # A nested savepoint contains the deliberately invalid INSERT.
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            new_id = await db_session.scalar(url_id_sequence.next_value())
            db_session.add(URL(id=new_id, original_url=ORIGINAL_URL, short_code=code))
            await db_session.flush()
    response = await client.get(f"/{code}")
    assert response.status_code == 302
