"""Read project-root .env settings once, before constructing shared resources."""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, RedisDsn, field_validator
from sqlalchemy import URL

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")  # Existing environment variables take precedence.


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    postgres_user: str
    postgres_password: str = Field(repr=False)
    postgres_db: str
    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    redis_url: RedisDsn = Field(repr=False)
    public_base_url: str = "http://localhost:8000"

    @field_validator("public_base_url")
    @classmethod
    def validate_public_base_url(cls, value: str) -> str:
        HttpUrl(value)
        return value.rstrip("/")

    @property
    def database_url(self) -> URL:
        # URL.create safely handles special characters in credentials.
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    def short_url(self, code: str) -> HttpUrl:
        return HttpUrl(f"{self.public_base_url}/{code}")


settings = Settings(
    postgres_user=os.environ["POSTGRES_USER"],
    postgres_password=os.environ["POSTGRES_PASSWORD"],
    postgres_db=os.environ["POSTGRES_DB"],
    postgres_host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
    postgres_port=os.getenv("POSTGRES_PORT", "5432"),
    redis_url=os.environ["REDIS_URL"],
    public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000"),
)
