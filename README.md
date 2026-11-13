# URL shortener

A learning project built with FastAPI, asynchronous SQLAlchemy, PostgreSQL,
and Redis. It creates short links, redirects visitors, selectively caches popular
destinations, and shares per-IP rate limits across application workers.

## Features

- Validated HTTP/HTTPS input and public short-link responses.
- PostgreSQL sequence IDs encoded using a fixed shuffled Base62 alphabet.
- Case-sensitive short codes; repeated URLs intentionally receive distinct codes.
- Popularity-based Redis caching with PostgreSQL fallback.
- Atomic Redis Lua token buckets, separated by client IP and operation.
- Alembic migrations and tests using real PostgreSQL, real Redis, and mocks.

## Local setup

Use Python 3.11 or later. Run the following commands from the project root.
The container examples use Podman; on macOS, start its Linux machine first.
Docker users can substitute `docker` for `podman` in the container commands.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Set a local password in `.env` before creating PostgreSQL. Keep `.env` private.
Existing environment variables take precedence over values in that file.

Create the containers once:

```sh
podman run --detach --name week1-postgres \
  --env-file .env \
  --publish 127.0.0.1:5432:5432 \
  --volume week1-postgres-data:/var/lib/postgresql/data \
  docker.io/library/postgres:17

podman run --detach --name week2-redis \
  --publish 127.0.0.1:6379:6379 \
  docker.io/library/redis:8
```

On subsequent runs, start the existing containers instead:

```sh
podman start week1-postgres week2-redis
```

After PostgreSQL is ready, apply migrations and start the application:

```sh
python -m alembic upgrade head
python -m uvicorn app.main:app --reload
```

Interactive API documentation: [localhost:8000/docs](http://localhost:8000/docs).
`--reload` is for local development.

The PostgreSQL image initializes the configured user as a superuser. This setup
is for local development; use a restricted application role for a deployment.
Changing `.env` does not change credentials in an already initialized volume.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `POSTGRES_USER` | Database user | Required |
| `POSTGRES_PASSWORD` | Database password | Required |
| `POSTGRES_DB` | Development database | Required |
| `POSTGRES_HOST` | Database host | `127.0.0.1` |
| `POSTGRES_PORT` | Database port | `5432` |
| `REDIS_URL` | Redis connection URL | Required |
| `PUBLIC_BASE_URL` | Prefix for returned short links | `http://localhost:8000` |
| `TEST_POSTGRES_DB` | Dedicated test database | `POSTGRES_DB` plus `_test` |

Settings are validated when imported. Restart the process after configuration
changes. The Redis URL must be valid even when Redis itself is unavailable.

## API

Create a link:

```sh
curl -i http://localhost:8000/urls \
  -H 'Content-Type: application/json' \
  -d '{"long_url":"https://example.com/articles/system-design"}'
```

Success returns `201` with `code` and `short_url`. Requesting `GET /{code}`
returns a `302` response whose `Location` header contains the destination.

| Outcome | Status |
|---|---:|
| Link created | 201 |
| Destination found | 302 |
| Invalid request body | 422 |
| Unknown code | 404 |
| Rate limit exceeded | 429 |
| Database creation or lookup failure | 500 |
| Client address unavailable to the limiter | 503 |

Database changes are committed before a creation response is returned. Failed
creation attempts trigger rollback. Consumed sequence IDs are not reclaimed.
The Base62 alphabet must remain stable once links exist; encoding is not
encryption or protection against enumeration.

## Caching and rate limiting

Popularity uses ten-minute windows aligned to Unix time. The top 20% of distinct
successfully resolved codes in the previous window, rounded up, qualify for
destination caching in the current window. An empty previous window admits none.
Ties are broken by descending code order.

Eligible destinations use window-specific keys and a five-minute TTL. Cache
hits do not extend that TTL. Missing or corrupt cached values fall back to
PostgreSQL. Successful resolutions increment popularity, including cache hits.
Unknown codes are not cached or counted.

| Operation | Route | Bucket capacity | Refill |
|---|---|---:|---:|
| Create | `POST /urls` | 10 | 10 tokens/minute |
| Redirect | `GET /{code}` | 50 | 50 tokens/minute |

Buckets are shared through Redis and keyed by operation and client IP. All codes
share an IP's redirect bucket. Tokens refill continuously and permit bursts;
these are not strict rolling-minute quotas. Allowed attempts consume a token
even if downstream lookup or validation fails. Rejections include a whole-second
`Retry-After` header.

Redis cache errors do not prevent a successful PostgreSQL lookup. Redis
connection failures and timeouts make the limiter fail open with a warning;
script errors propagate. Startup also tolerates Redis connection failure.
Socket timeouts are 200 ms per operation, with no retries, rather than a total
request deadline. Shutdown closes Redis and disposes the database engine.

Client identity comes from `request.client.host`. Behind a proxy, configure
trusted proxy addresses in Uvicorn, sanitize forwarding headers at the proxy,
and restrict direct application access. Clients sharing a public IP share limits.

## Tests

With PostgreSQL and Redis running:

```sh
python scripts/prepare_test_database.py
python -m pytest -q
```

The helper creates a separate test database if needed; it does not drop or clear
a database. The configured user needs permission to create it. A custom test
database must end in `_test` and differ from the development database.

Tests apply real migrations. API tests use an outer transaction and session
savepoints so fixture teardown can roll back changes despite application commits.
Sequence increments may leave gaps. Run this database test setup sequentially.

HTTPX calls FastAPI in process. General API tests override resource dependencies
and mock Redis. Dedicated Redis tests exercise TTL expiry, popularity ranking,
Lua execution, and concurrent token consumption using isolated key prefixes.
Separate lifecycle tests cover startup and shutdown.

Focused tests:

```sh
python -m pytest tests/test_base62.py tests/test_token_bucket.py -q
python -m pytest tests/test_rate_limit.py -q
python -m pytest tests/test_cache.py::test_query_reduction_for_eligible_link -q -s
```

The Base62 and standalone token-bucket tests need no running services, but test
configuration still loads `.env`. The standalone in-memory bucket is a learning
component; HTTP requests use the Redis-backed limiter.

## Project structure

```text
app/
  main.py                 Application assembly and resource lifecycle
  api/dependencies.py     Sessions, shared Redis access, limiter dependencies
  api/routes/urls.py      HTTP routes and error translation
  core/                   Validated settings and Redis client construction
  db/session.py           Database engine and session factory
  models/url.py           ORM model and ID sequence
  schemas/url.py          Request, response, and lookup validation
  services/urls.py        Creation, transaction handling, and cache-aside lookup
  services/popularity.py  Windowed rankings and access counters
  services/rate_limit.py  Shared Redis Lua token buckets
  services/token_bucket.py Standalone in-memory learning implementation
  utils/base62.py         Stable ID encoding
migrations/               Alembic schema history
scripts/                  Local Redis check and test database preparation
tests/                    Unit and integration tests
```

## Scope and limitations

This is a learning project, not a hardened public service. It has no
authentication, destination-update endpoint, or cache stampede lock. Direct
database updates may remain stale in the cache until expiry or a window change.
Redis outages suspend rate-limit enforcement. Transaction-isolated API tests do
not prove commit visibility from a separate connection, and the concurrency tests
use separate clients in one process rather than multiple deployed workers.

The query-reduction test compares 20 forced-miss requests with 20 requests using
an initially empty eligible cache. Its expected mapping SELECT counts are 20 and
1, respectively. That controlled result describes database-query avoidance, not
a production latency improvement.

Earlier milestones and detailed learning notes are preserved in
[docs/learning-notes.md](docs/learning-notes.md). They describe historical stages;
this README describes current behavior.
