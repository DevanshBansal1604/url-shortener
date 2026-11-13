"""Verify configuration validation and application assembly without PostgreSQL."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.db.session import engine
from app.main import app
from importlib import import_module

main_module = import_module("app.main")


def make_settings(**overrides):
    return Settings(**{
        "postgres_user": "test",
        "postgres_password": "p@ss:/word",
        "postgres_db": "test_db",
        "redis_url": "redis://127.0.0.1:6379/0",
        **overrides,
    })


@pytest.mark.parametrize("overrides", [
    {"public_base_url": "not-a-url"},
    {"public_base_url": "ftp://example.com"},
    {"redis_url": "not-a-url"},
    {"redis_url": "http://localhost:6379"},
    {"postgres_port": 0},
    {"postgres_port": 65536},
])
def test_invalid_settings_rejected(overrides):
    with pytest.raises(ValidationError):
        make_settings(**overrides)


def test_settings_preserve_credentials_and_build_public_link():
    settings = make_settings(public_base_url="https://sho.rt/")
    assert settings.database_url.password == "p@ss:/word"
    assert "p@ss:/word" not in repr(settings)
    assert str(settings.short_url("lI")) == "https://sho.rt/lI"


def test_app_registers_routes_and_disposes_engine_on_shutdown(monkeypatch):
    redis_client = AsyncMock()
    monkeypatch.setattr(main_module, "create_redis_client", lambda: redis_client)
    dispose = AsyncMock()
    monkeypatch.setattr(type(engine), "dispose", dispose)
    with TestClient(app) as client:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]
        assert "post" in paths["/urls"]
        assert "get" in paths["/{code}"]
        redis_client.ping.assert_awaited_once()
        assert app.state.redis is redis_client
        redis_client.aclose.assert_not_awaited()
        dispose.assert_not_awaited()
    redis_client.aclose.assert_awaited_once()
    dispose.assert_awaited_once()


def test_failed_redis_startup_still_cleans_up_resources(monkeypatch):
    redis_client = AsyncMock()
    from redis.exceptions import ConnectionError as RedisConnectionError
    redis_client.ping.side_effect = RedisConnectionError("Redis unavailable")
    monkeypatch.setattr(main_module, "create_redis_client", lambda: redis_client)
    dispose = AsyncMock()
    monkeypatch.setattr(type(engine), "dispose", dispose)
    with TestClient(app) as client:
        assert client.get("/openapi.json").status_code == 200
    redis_client.aclose.assert_awaited_once()
    dispose.assert_awaited_once()
