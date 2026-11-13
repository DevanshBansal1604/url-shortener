"""Exercise the application's Redis client without changing URL mappings.

Run from the project root: python -m scripts.check_redis
"""

import asyncio
from uuid import uuid4

from app.main import app, lifespan


async def main():
    # Use the same startup and shutdown path as the running FastAPI app.
    async with lifespan(app):
        client = app.state.redis
        key = f"practice:url:{uuid4().hex}"
        destination = "https://example.com/article"
        print("Redis connection: OK")
        try:
            await client.set(key, destination, ex=5)
            value = await client.get(key)
            remaining = await client.ttl(key)
            print(f"Immediate GET: {value}")
            print(f"TTL: {remaining} seconds")
            if value != destination or not 0 <= remaining <= 5:
                raise RuntimeError("Redis did not return the expected value and TTL")
            await asyncio.sleep(6)
            value = await client.get(key)
            remaining = await client.ttl(key)
            print(f"GET after expiry: {value}")
            print(f"TTL after expiry: {remaining}")
            if value is not None or remaining != -2:
                raise RuntimeError("The practice key did not expire as expected")
        finally:
            # Only remove this run's unique practice key, even if interrupted.
            await client.delete(key)
    print("Application resources: closed")


if __name__ == "__main__":
    asyncio.run(main())
