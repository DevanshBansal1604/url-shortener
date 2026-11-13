"""Run a read-only query through the Mac's published PostgreSQL port."""

import asyncio

from sqlalchemy import text

from app.db.session import engine


async def main() -> None:
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT current_database() AS database, current_user AS username")
            )
            row = result.mappings().one()
            print(f"Connected to {row['database']} as {row['username']}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
