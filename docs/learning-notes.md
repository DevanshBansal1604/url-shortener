> Historical learning notes: these sections describe successive milestones,
> including behavior later replaced. See the root README for current behavior.

# URL shortener

Current behavior: popularity-based caching and shared per-IP, per-operation
Redis rate limiting, verified through Week 2 Day 6 integration tests.
The Week 1 and Week 2 Day 1/2 sections below describe earlier milestones.

## Week 2 Day 4: token bucket fundamentals

`app/services/token_bucket.py` contains a standalone `TokenBucket`. It starts
with 50 tokens, replenishes at 50/60 tokens per second, and caps the balance at
50. Each `allow()` call replenishes first, saves the new balance and timestamp,
then consumes one token if available. Denied attempts retain fractional credits.
Refill is calculated on demand; no timer or background task is required.

The default clock is `time.monotonic`. Tests inject a callable fake clock
returning seconds, so they can advance time without sleeping. A backwards clock
raises `ValueError` before changing state.

For example, after consuming all 50 tokens at time zero, a request at 0.6
seconds is denied with 0.5 tokens retained. At 1.2 seconds a request succeeds,
leaving zero tokens. A bucket with 40 tokens and 30 seconds of idle time reaches
50, then an accepted request leaves 49; excess refill is discarded.

Capacity controls the immediate burst; refill controls the sustained rate.
Fifty requests at time zero and 25 at time 30 can all pass. This is different
from a strict maximum of 50 requests in every rolling 60-second interval.

Run the focused tests from the project root with the virtual environment active:

```sh
python -m pytest tests/test_token_bucket.py -q
```

These tests need neither Redis nor PostgreSQL running. They cover exhaustion,
fractional refill after denial, just-before/exact refill boundaries, capacity,
long idle periods, burst allowance, clock initialization, independent instances,
and backwards-clock rejection. Balances use floating-point arithmetic; tests
use approximate assertions for fractional balances.

This component is local in-memory state. It is not wired into HTTP requests,
does not identify IPs, and does not coordinate workers. Redis-backed per-IP
state, atomic updates, 429 responses, and outage policy are Day 5 work.

## Day 3: create a short link

Activate `.venv`, install `requirements.txt`, and apply the existing migration
with `alembic upgrade head` if it has not been applied. Start the API:

```sh
python -m uvicorn app.main:app --reload
```

Open http://localhost:8000/docs, or send:

```sh
curl -i http://localhost:8000/urls \
  -H 'Content-Type: application/json' \
  -d '{"long_url":"https://example.com/articles/system-design"}'
```

A successful request returns `201 Created` with `code` and `short_url`.
Invalid HTTP/HTTPS URLs return `422` before the route body runs.
Repeated submissions intentionally create separate rows and different codes.
The GET /{code} endpoint returns a 302 redirect to the saved original URL; unknown codes return 404.

The creation flow is: validate input, allocate `url_id_seq`, encode the ID,
add a complete row, commit, then return success. Database failures trigger a
rollback and a generic `500` response. Sequence values consumed by failed
requests are not reclaimed.

`app/utils/base62.py` contains a fixed shuffled alphabet. It changes the visible ID
representation but does not prevent enumeration: adjacent IDs still have
related codes. Keep the alphabet unchanged once links have been created.
With this alphabet ID 64 encodes as `lI`, rather than the conventional `12`.
Codes remain case-sensitive. No schema migration is needed.

`PUBLIC_BASE_URL` optionally sets the public link origin; the default is
`http://localhost:8000`. It can be set in `.env` before starting the app.

`app/main.py` assembles the application and includes the URL router.
Routes, schemas, services, configuration, and database setup have separate modules.

Week 1 learning project. Start with PostgreSQL, then add SQLAlchemy and Alembic.

## Local environment

From this directory, activate the existing Python virtual environment:

```sh
source .venv/bin/activate
```

`.venv` holds this project's Python interpreter and installed packages. Run `deactivate` to leave it.

`.env` contains local PostgreSQL credentials. It is ignored by Git. `.env.example` shows the configuration keys without the actual password.

Install the database dependencies inside the virtual environment:

```sh
python -m pip install -r requirements.txt
```

With PostgreSQL running, verify the connection from Python on your Mac:

```sh
python check_db.py
```

Expected output: `Connected to url_shortener as shortener`.

`app/core/config.py` loads and validates local settings. `app/db/session.py` configures the async engine and session factory. `check_db.py` borrows a connection, runs a read-only query, then closes the connection pool before exiting.

## PostgreSQL

On macOS, open Podman Desktop and start `podman-machine-default` using **Run Podman** if it is stopped. Container commands need this Linux VM running.

The database container is created with:

```sh
podman run --detach \
  --name week1-postgres \
  --env-file .env \
  --publish 127.0.0.1:5432:5432 \
  --volume week1-postgres-data:/var/lib/postgresql/data \
  docker.io/library/postgres:17
```

Run that command only for initial creation. After creation, use `podman start week1-postgres` to start the existing container.

- `--detach`: run PostgreSQL in the background.
- `--name`: give the container a stable name.
- `--env-file`: load the database name, user, and password for initial setup.
- `--publish`: forward port 5432 on the Mac's loopback address to PostgreSQL's port in the container.
- `--volume`: keep database files in a named volume, separate from the container.
- `postgres:17`: use major version 17 of the official PostgreSQL image.

The database name is `url_shortener`, the local development user is `shortener`, and the host address is `127.0.0.1:5432`. This user is initialized as a database superuser by the image; a production deployment needs a separate restricted application role.

Check running containers and database readiness:

```sh
podman ps
podman exec week1-postgres pg_isready -U shortener -d url_shortener
```

Open the SQL terminal:

```sh
podman exec -it week1-postgres psql -U shortener -d url_shortener
```

Then try:

```sql
SELECT current_database(), current_user;
\dt
\q
```

`\dt` lists tables. The project starts with no application tables; Alembic will create them in the next step. `\q` exits psql.

Stop the database without deleting data:

```sh
podman stop week1-postgres
```

Restart it later:

```sh
podman start week1-postgres
```

Stopping the VM or container preserves the named volume. The image's initialization variables apply when the data directory is first initialized; editing `.env` later does not change an existing database password.

References: [Podman on macOS](https://podman.io/docs/installation), [official PostgreSQL image](https://hub.docker.com/_/postgres).


## Day 6: automated tests

From the project directory, activate `.venv`, then run:

```sh
python -m pip install -r requirements-dev.txt
python scripts/prepare_test_database.py
python -m pytest -q
```

PostgreSQL must be running. The setup helper creates `url_shortener_test`
(by default, your configured `POSTGRES_DB` plus `_test`). It never drops or
clears a database. The configured PostgreSQL user must have CREATEDB permission.
To select another dedicated test database, set `TEST_POSTGRES_DB`; it must end
in `_test` and differ from the development database. Do not point it at valuable
data. No changes to `.env` are needed.

Before importing the app, `tests/conftest.py` selects the test database for the
pytest process. API tests apply existing Alembic migrations there once per run.
Each API test owns an outer transaction, and the application session uses
`join_transaction_mode="create_savepoint"`. Endpoint commits release savepoints;
fixture cleanup rolls back the outer transaction. Sequence increments can still
leave ID gaps. HTTPX calls the app in memory without starting Uvicorn.

Fixture flow: `test_engine` -> `db_session` -> `client` -> test; cleanup runs in
reverse. The client overrides `get_session` so requests and assertions share
the fixture session. `yield` pauses a fixture for the test, then resumes cleanup.

Coverage includes persisted mappings, redirects, missing codes, invalid payloads,
repeated submissions, PostgreSQL unique constraints, Base62 boundaries, and
generic database error responses. Failure tests use AsyncMock to force commit
or lookup errors. A rollback spy uses `wraps` to execute real rollback while
recording whether the endpoint awaited it.

These transaction-isolated tests do not prove outer-commit visibility from an
independent connection. ASGITransport also does not run application lifespan;
these tests cover route/database behavior, not startup or shutdown.

Run only the database-free unit tests:

```sh
python -m pytest tests/test_base62.py -q
```

To observe test names and parametrized cases, use `python -m pytest -v`.
The database helper is safe to rerun, and subsequent test runs reapply only
outstanding migrations. Run database tests sequentially with this setup.


## Day 7: modular refactoring

```text
app/
  main.py                 Application assembly and engine cleanup
  api/dependencies.py     Request-scoped database session
  api/routes/urls.py      POST /urls and GET /{code}; HTTP error translation
  core/config.py          Validated environment settings and project-root .env
  db/session.py           Async engine and session factory
  models/url.py           SQLAlchemy Base, URL table, and ID sequence
  schemas/url.py          Pydantic request/response contracts
  services/urls.py        Mapping creation, transaction ownership, and lookup
  utils/base62.py         Stable shuffled Base62 encoding
```

Run all commands from the project root. Start with
`python -m uvicorn app.main:app --reload`. Endpoint paths and database schema
are unchanged; no new migration is needed. Existing migrations import the moved
model metadata, including the ID sequence.

Settings are loaded once on import; environment variables override `.env`.
Invalid public URL or port settings fail immediately. Select the database before
importing the app, then restart the process after changing configuration.

Routes validate HTTP input and translate service errors to HTTP responses.
Services receive an injected session, own creation commit/rollback, and have no
FastAPI imports. A CLI caller can reuse `create_mapping` but must validate its
input first. The dependency closes the session; it does not commit.

Tests override the exact `get_session` callable imported by the router. The
existing transaction/savepoint isolation is preserved. Run `python -m pytest -q`
with PostgreSQL running. `resolve_mapping` is the entry point for Week 2's cache
lookup; Redis is not introduced by this refactor.


## Week 2, Day 1: Redis foundations

Continue in this project directory. Activate `.venv` and install the updated
requirements:

```sh
source .venv/bin/activate
python -m pip install -r requirements.txt
```

With the Podman machine running, create the local Redis container once:

```sh
podman run --detach --name week2-redis --publish 127.0.0.1:6379:6379 docker.io/library/redis:8
```

For later sessions, use `podman start week2-redis`. To stop it, use
`podman stop week2-redis`. These practice cache entries need no persistent volume.
The port is exposed only on your Mac's loopback address.

`REDIS_URL` is required. Set `REDIS_URL=redis://127.0.0.1:6379/0` in `.env`
for the local container. There is no application fallback; a missing or invalid
setting prevents startup.
The `/0` selects logical Redis database zero, independent of PostgreSQL.
The async Python client is separate from the Redis server. It reuses connections
through its pool, and decodes string values into Python strings.

Application startup creates the client, sends PING, and stores it on
`app.state.redis`. Shutdown closes the client and disposes the PostgreSQL engine.
For this Day 1 exercise, unreachable Redis prevents application startup. Later
cache resilience work will introduce fallback behavior. Closing a client does
not stop Redis or delete its keys.

Run the exercise using the application's actual resource lifecycle:

```sh
python -m scripts.check_redis
```

It writes a unique `practice:url:<id>` key with a five-second TTL, reads it,
waits six seconds, and verifies GET returns `None` and TTL returns `-2`.
Only its own practice key is cleaned up. A normal GET does not refresh the TTL.
TTL `-1` means an existing key has no expiry; `-2` means the key is absent.

Redirects still query PostgreSQL. Cache-aside integration in `resolve_mapping`
is Day 2; popularity admission and outage handling follow on Day 3.
The lifecycle unit tests mock Redis so they do not require a live Redis server;
this exercise verifies the real connection and expiry.


## Week 2, Day 2: cache-aside lookup

Use the project root directory.
Start commands from that directory and activate `.venv` as usual.

`resolve_mapping` first reads `url:<code>` from Redis. Hits are validated from
JSON into `URLMapping`; misses query PostgreSQL and validate the row into the
same model. Successful lookups are cached as JSON with a 300-second TTL.
Hits do not refresh the TTL. Missing mappings are not cached. The route still
returns 302 for found destinations, 404 for unknown codes, and a generic 500
for database failures. Input validation and the SQLAlchemy model are unchanged.
No database migration is needed.

Run `python -m pytest -q` with PostgreSQL and Redis running. API tests use the
isolated test database and mock Redis by default. The real Redis cache test
uses a newly created test row, removes only its own key, and verifies a miss,
a hit without another mapping query, and an expired key causing a new query.
It temporarily uses a one-second TTL; production code defaults to 300 seconds.

Day 2 caches all successful lookups. Popularity admission and Redis outage
fallback are Day 3 work. Redis is still required at startup; cache errors return
a generic 500 today. Invalid cached JSON or a mismatched code also returns 500.
There is no stampede lock: concurrent misses may query PostgreSQL more than once.
There is no destination-update endpoint yet; a future update path needs cache
invalidation after the database commit. TTL alone permits stale destinations.


## Week 2 Day 3: popular links and resilience

`app/services/popularity.py` tracks successful resolutions in Redis sorted sets.
Every successful redirect contributes one count, including destination-cache hits.
Unknown codes and database failures contribute none. Redis counter failures are
logged and skipped so a redirect can still succeed.

Policy:

- Use fixed 600-second windows aligned to Unix time, shared across app workers.
- Rank the previous completed window's distinct successfully accessed codes.
- Admit `ceil(N / 5)` links; an empty window admits none.
- Order by descending count, breaking ties by descending code (Redis ordering).
- Read rank and population on demand; the window boundary automatically refreshes
  admission without a background job. This uses ZCARD and ZREVRANK rather than
  materializing a separate eligible set.
- Start without winners when no previous-window counts exist. That includes a
  cold Redis start. Newly popular links wait until the next boundary.
- Store destinations under window-specific keys with a 300-second TTL. A new
  window ignores prior-window destinations; old entries expire naturally.
  Consequently, a still-popular link also has a cold lookup at a window boundary.
- Cache misses fetch PostgreSQL, and only eligible successful results are cached.
  Hits never extend TTL. Expiry triggers no proactive fetch; the next request loads.
- Counters expire at the end of the following window, bounding retained history.
  Counts use successful resolution time, rather than initial arrival time.

Keys: `popular:v1:counts:<window>` stores counts for all valid accessed codes;
`popular:v1:dest:<window>:<code>` stores destinations only for qualifying codes.
Earlier `url:<code>` keys are ignored and naturally expire under their old TTL.

Redis connections and reads have 200 ms socket timeouts and no retries. Each
operation has its own timeout; this is not a total request deadline. Redis read,
write, and counter errors cannot suppress a successful PostgreSQL resolution.
The app also starts if Redis is unavailable. Database errors retain generic 500
responses; unknown codes retain 404; successful lookups retain 302. Corrupt cache
entries are treated as misses and repaired from the database when eligible.

Staleness: changing a destination directly in PostgreSQL does not update Redis.
An existing cached value may be returned until expiry (or the next window).
An update flow should delete the current destination key after committing the
change. There is no destination-update endpoint in this project yet. This exercise
does not provide strict consistency for concurrent updates or stampede protection.

Verification: activate `.venv` and run `python -m pytest -q`, with local PostgreSQL,
the separate migrated test database, and Redis available as in the setup above.
Tests use unique Redis prefixes and remove only their own keys. They cover top-20%
selection, tie-breaking, window transitions, cache-hit counting, admission, real
expiry, database query avoidance, stale values and invalidation, Redis operation
failures, an unreachable Redis endpoint, startup fallback, and existing API behavior.


## Week 2 Day 5: shared per-IP rate limiting

`app/services/rate_limit.py` executes one atomic Redis Lua script per request.
It reads Redis server TIME, refills fractional tokens, consumes one if available,
and saves both balance and timestamp, including on denial. Concurrent workers
share state; no worker-local fallback bucket exists. Backward clock adjustments
are clamped so they do not generate credit twice.

| Route | Operation | Capacity | Refill |
|---|---|---:|---:|
| POST /urls | create | 10 | 10 tokens/minute |
| GET /{code} | redirect | 50 | 50 tokens/minute |

Keys are `rate_limit:v1:<operation>:<client-ip>` Redis hashes containing `tokens`
and `last_updated`. All redirect codes share the same per-IP redirect bucket.
Each update refreshes expiry to the empty-to-full duration (60 seconds for both
policies). A missing bucket starts full. Limits allow bursts, not strict rolling
minute quotas. Every attempt reaching the dependency costs one token if admitted,
including unknown redirect codes and downstream errors. Rejected requests do not
run the route action. Validation failures can also consume admission credit.

Denied requests return 429 and a Retry-After header rounded up to whole seconds.
Redis connection/timeouts fail open with a warning: enforcement pauses, while
normal route/cache/PostgreSQL behavior continues. An ambiguous timeout may have
already consumed a token. Script errors propagate instead of silently disabling
the limiter. The existing Redis client has bounded socket timeouts and no retries.

Client identity uses `request.client.host`; never parse client-supplied forwarding
headers in the limiter. Behind a proxy, configure Uvicorn's proxy-header trust
for the known proxy addresses, configure that proxy to sanitize forwarding
headers, and restrict direct access to the app. Users sharing a public IP share
its buckets. Missing client addresses receive 503.

Run `python -m pytest -q` with the configured Redis and dedicated PostgreSQL test
database running. Dedicated limiter tests use unique key prefixes and clean up
only their own keys. Separate Redis clients verify shared-state concurrency;
HTTP tests exercise both route dependencies without needing database writes.


## Week 2 Day 6: integration tests and verification

Verified on 12 November 2026 with local PostgreSQL and real Redis:
**68 passed, 1 warning**. The warning is an upstream Starlette TestClient/httpx
deprecation; no test failed.

With PostgreSQL and Redis running, activate `.venv` and run:

```sh
python scripts/prepare_test_database.py
python -m pytest tests/test_cache.py tests/test_popularity.py tests/test_rate_limit.py tests/test_token_bucket.py -q
python -m pytest -q
```

The database helper is needed if the dedicated test database has not been created.
Existing fixtures migrate that database and roll back each API test's transaction.
Real Redis tests use unique prefixes and delete only their own keys.

Coverage includes cache hits/misses, real TTL expiry, previous-window top-20%
admission, unknown codes, corrupt cached values, Redis outage fallback, stale
destinations and explicit invalidation, independent IP and operation buckets,
fractional refill, atomic concurrent consumption, HTTP 429 with Retry-After,
and limiter fail-open behavior. General API tests mock Redis; dedicated tests
exercise real Redis expiry, ranking, Lua execution, and shared clients.

### Observed PostgreSQL lookup reduction

Run the comparison with its measured output visible:

```sh
python -m pytest tests/test_cache.py::test_query_reduction_for_eligible_link -q -s
```

The test creates one real mapping, seeds previous-window eligibility in Redis,
fixes the application popularity window, and counts actual mapping SELECTs via
SQLAlchemy's `before_cursor_execute` event. Setup queries are excluded.

| Scenario | Requests | Mapping SELECTs observed |
|---|---:|---:|
| Forced miss before every request | 20 | 20 |
| Caching with an initially empty destination cache | 20 | 1 |
| Further requests with the cache warm | 5 | 0 |

The forced-miss baseline deletes the destination key before each request; it
retains the same eligibility, limiter, and popularity behavior as the cached
case. The cached phase clears the key once, then allows normal cache-aside
behavior. Every response must be 302 with the correct destination. The test
also verifies all 45 successful resolutions contribute to popularity, the key
retains a positive TTL, and total redirects stay within the 50-token burst.

Observed mapping-query reduction for the two 20-request phases:
`(20 - 1) / 20 * 100 = 95%`.

This controlled repeated-link workload demonstrates query avoidance, not a
95% latency improvement or a production-wide load estimate. Noneligible links,
expiry, outages, window transitions, and concurrent cold misses can increase
PostgreSQL work. There is no cache stampede lock. The comparison uses real
Redis and PostgreSQL but sequential in-process HTTP requests; the separate
concurrency test verifies atomic admission across two Redis clients, rather
than running multiple server processes. ASGITransport does not run application
lifespan automatically. Startup/shutdown coverage comes from separate lifecycle
tests. Day 7 remains the detailed implementation walkthrough.
