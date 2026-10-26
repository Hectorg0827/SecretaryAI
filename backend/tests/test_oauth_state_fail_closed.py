"""Regression coverage: OAuth state must be Redis-backed and single-use."""

import pytest
from fastapi import HTTPException

from app.api import auth


class InMemoryRedisDouble:
    def __init__(self):
        self.values = {}
        self.ttls = []

    def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls.append(ttl)
        return True

    def getdel(self, key):
        return self.values.pop(key, None)

    def get(self, key):
        raise AssertionError("Non-atomic GET must not be used for OAuth state")

    def delete(self, key):
        raise AssertionError("Non-atomic DELETE must not be used for OAuth state")


class BrokenRedis:
    def setex(self, *_args):
        raise ConnectionError("contains-private-redis-connection-details")

    def getdel(self, *_args):
        raise ConnectionError("contains-private-redis-connection-details")


def test_oauth_state_is_centralized_single_use_and_has_ttl(monkeypatch):
    redis = InMemoryRedisDouble()
    monkeypatch.setattr(auth, "_redis", lambda: redis)

    auth._state_set("random-state", "company-1")
    assert redis.ttls == [600]
    assert auth._state_pop("random-state") == "company-1"
    assert auth._state_pop("random-state") is None


def test_oauth_start_refuses_redis_outage_without_fallback(monkeypatch):
    monkeypatch.setattr(auth, "_redis", lambda: BrokenRedis())

    with pytest.raises(HTTPException) as error:
        auth._state_set("random-state", "company-1")

    assert error.value.status_code == 503
    assert "connection-details" not in str(error.value.detail)
    assert not hasattr(auth, "_oauth_states_fallback")


def test_oauth_callback_refuses_redis_outage_without_fallback(monkeypatch):
    monkeypatch.setattr(auth, "_redis", lambda: BrokenRedis())

    with pytest.raises(HTTPException) as error:
        auth._state_pop("random-state")

    assert error.value.status_code == 503
    assert "connection-details" not in str(error.value.detail)


def test_oauth_start_refuses_redis_write_rejection(monkeypatch):
    redis = InMemoryRedisDouble()
    redis.setex = lambda *_args: False
    monkeypatch.setattr(auth, "_redis", lambda: redis)

    with pytest.raises(HTTPException) as error:
        auth._state_set("random-state", "company-1")

    assert error.value.status_code == 503
