"""Create a separate test database, without dropping or clearing any database."""

import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv


async def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    development_db = os.environ["POSTGRES_DB"]
    test_db = os.getenv("TEST_POSTGRES_DB", f"{development_db}_test")
    if test_db == development_db or not test_db.endswith("_test"):
        raise ValueError("TEST_POSTGRES_DB must differ from POSTGRES_DB and end in _test")
    if len(test_db.encode("utf-8")) > 63:
        raise ValueError("Test database name exceeds PostgreSQL's identifier limit")
    connection = await asyncpg.connect(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        database="postgres",
    )
    try:
        exists = await connection.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", test_db
        )
        if not exists:
            quoted_name = '"' + test_db.replace('"', '""') + '"'
            await connection.execute(f"CREATE DATABASE {quoted_name}")
            print(f"Created test database: {test_db}")
        else:
            print(f"Test database already exists: {test_db}")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
