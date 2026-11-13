"""Deterministic unit tests: no sleeping, Redis, or PostgreSQL required."""

import pytest

from app.services.token_bucket import TokenBucket


class FakeClock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def bucket(clock):
    return TokenBucket(clock=clock)


def empty(bucket):
    assert all(bucket.allow() for _ in range(50))
    assert bucket.tokens == 0


def test_initial_burst_and_exhaustion(bucket):
    empty(bucket)
    assert bucket.allow() is False
    assert bucket.tokens == 0


def test_denial_preserves_fractional_refill_and_updates_timestamp(bucket, clock):
    empty(bucket)
    clock.now = 0.6
    assert bucket.allow() is False
    assert bucket.tokens == pytest.approx(0.5)
    assert bucket.last_updated == 0.6
    clock.now = 1.2
    assert bucket.allow() is True
    assert bucket.tokens == pytest.approx(0)


@pytest.mark.parametrize("elapsed, allowed", [(1.199999, False), (1.2, True)])
def test_one_token_refill_boundary(bucket, clock, elapsed, allowed):
    empty(bucket)
    clock.now = elapsed
    assert bucket.allow() is allowed


def test_capacity_discards_excess_credit(bucket, clock):
    assert all(bucket.allow() for _ in range(10))
    assert bucket.tokens == 40
    clock.now = 30
    assert bucket.allow() is True
    assert bucket.tokens == 49
    assert all(bucket.allow() for _ in range(49))
    assert bucket.allow() is False


def test_long_idle_refills_empty_bucket_only_to_capacity(bucket, clock):
    empty(bucket)
    clock.now = 120
    empty(bucket)
    assert bucket.allow() is False


def test_refill_allows_more_than_fifty_in_a_rolling_minute(bucket, clock):
    empty(bucket)
    clock.now = 30
    assert all(bucket.allow() for _ in range(25))
    assert bucket.allow() is False


def test_creation_time_is_refill_baseline():
    clock = FakeClock(1000)
    bucket = TokenBucket(clock=clock)
    empty(bucket)
    assert bucket.allow() is False
    clock.now += 6
    assert all(bucket.allow() for _ in range(5))
    assert bucket.allow() is False


def test_instances_have_independent_balances(clock):
    first = TokenBucket(clock=clock)
    second = TokenBucket(clock=clock)
    empty(first)
    assert first.allow() is False
    assert second.allow() is True
    assert second.tokens == 49


def test_backwards_clock_is_rejected_without_changing_state(bucket, clock):
    assert bucket.allow() is True
    clock.now = -1
    with pytest.raises(ValueError, match="backwards"):
        bucket.allow()
    assert bucket.tokens == 49
    assert bucket.last_updated == 0
