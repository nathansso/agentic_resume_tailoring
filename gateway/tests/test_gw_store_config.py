"""The KV store adapters and the environment settings."""
import json
from decimal import Decimal

import pytest

from artie_gateway.config import DEFAULT_DATASET_ID, Config
from artie_gateway.errors import ConfigError
from artie_gateway.store import InMemoryStore, StoreUnavailable, UpstashStore
from artie_gateway.transport import HttpResult, TransportError, TransportTimeout

from gw_fakes import Clock


# --- in-memory store -----------------------------------------------------------------------

def test_incr_counts_and_expires():
    clock = Clock()
    store = InMemoryStore(clock)
    assert [store.incr("k", 10) for _ in range(3)] == [1, 2, 3]
    clock.now += 11
    assert store.get("k") is None
    assert store.incr("k", 10) == 1


def test_decr_never_goes_below_zero_or_creates_a_key():
    store = InMemoryStore(Clock())
    store.decr("missing")
    assert store.get("missing") is None
    store.incr("k", 10)
    store.decr("k")
    store.decr("k")
    assert store.get("k") == "0"


def test_set_and_get_with_expiry():
    clock = Clock()
    store = InMemoryStore(clock)
    store.set("a", "x", 5)
    assert store.get("a") == "x"
    clock.now += 6
    assert store.get("a") is None


# --- Upstash REST store (against a fake Redis) -----------------------------------------------

class FakeRedis:
    """Just enough of Upstash's REST API: single commands and /pipeline."""

    def __init__(self):
        self.data = {}
        self.requests = []
        self.fail = None

    def __call__(self, method, url, headers, body, timeout):
        self.requests.append((method, url, dict(headers), json.loads(body)))
        if self.fail == "timeout":
            raise TransportTimeout()
        if self.fail == "unreachable":
            raise TransportError()
        if self.fail == "500":
            return HttpResult(500, b"detail")
        payload = json.loads(body)
        if url.endswith("/pipeline"):
            return HttpResult(200, json.dumps([{"result": self.run(c)} for c in payload]).encode())
        return HttpResult(200, json.dumps({"result": self.run(payload)}).encode())

    def run(self, command):
        op, key, *rest = command
        if op == "INCR":
            self.data[key] = int(self.data.get(key, 0)) + 1
            return self.data[key]
        if op == "DECR":
            self.data[key] = int(self.data.get(key, 0)) - 1
            return self.data[key]
        if op == "GET":
            return None if key not in self.data else str(self.data[key])
        if op == "SET":
            self.data[key] = rest[0]
            return "OK"
        if op == "EXPIRE":
            return 1
        raise AssertionError(command)


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
def upstash(redis):
    return UpstashStore("https://example-kv.upstash.io/", "kv-token", redis)


def test_upstash_incr_sets_an_expiry_in_the_same_round_trip(upstash, redis):
    assert upstash.incr("quota:1", 600) == 1
    assert upstash.incr("quota:1", 600) == 2
    method, url, headers, body = redis.requests[0]
    assert (method, url) == ("POST", "https://example-kv.upstash.io/pipeline")
    assert headers["Authorization"] == "Bearer kv-token"
    assert body == [["INCR", "quota:1"], ["EXPIRE", "quota:1", 600]]


def test_upstash_get_set_and_guarded_decr(upstash, redis):
    assert upstash.get("a") is None
    upstash.set("a", "7", 60)
    assert upstash.get("a") == "7"
    upstash.decr("missing")
    assert "missing" not in redis.data                    # no -1 key is created
    upstash.decr("a")
    assert redis.data["a"] == 6
    assert ["SET", "a", "7", "EX", 60] in [r[3] for r in redis.requests]


@pytest.mark.parametrize("failure", ["timeout", "unreachable", "500"])
def test_upstash_failures_become_store_unavailable(upstash, redis, failure):
    redis.fail = failure
    for call in (lambda: upstash.incr("k", 1), lambda: upstash.get("k"), lambda: upstash.set("k", "v", 1)):
        with pytest.raises(StoreUnavailable):
            call()


# --- Config ----------------------------------------------------------------------------------

def test_defaults():
    config = Config.from_env({"BRIGHTDATA_API_KEY": "k"})
    assert config.daily_quota == 3
    assert config.brightdata_dataset_id == DEFAULT_DATASET_ID
    assert config.monthly_cap_usd == Decimal("20")
    assert config.max_calls_per_month == int(Decimal("20") // Decimal("0.0015"))
    assert config.kv_url is None and config.allow_memory_store is False


def test_every_documented_variable_is_read():
    config = Config.from_env({
        "BRIGHTDATA_API_KEY": "key",
        "BRIGHTDATA_LINKEDIN_DATASET_ID": "gd_other",
        "KV_REST_API_URL": "https://kv.example",
        "KV_REST_API_TOKEN": "tok",
        "ART_GATEWAY_DAILY_QUOTA": "5",
        "ART_GATEWAY_MONTHLY_CAP_USD": "12.5",
        "ART_GATEWAY_COST_PER_RECORD_USD": "0.5",
    })
    assert (config.brightdata_api_key, config.brightdata_dataset_id) == ("key", "gd_other")
    assert (config.kv_url, config.kv_token) == ("https://kv.example", "tok")
    assert config.daily_quota == 5
    assert config.max_calls_per_month == 25


def test_the_upstash_variable_names_work_too():
    config = Config.from_env({"UPSTASH_REDIS_REST_URL": "https://u.example", "UPSTASH_REDIS_REST_TOKEN": "t"})
    assert (config.kv_url, config.kv_token) == ("https://u.example", "t")


@pytest.mark.parametrize(
    "name,value",
    [
        ("ART_GATEWAY_DAILY_QUOTA", "three"),
        ("ART_GATEWAY_DAILY_QUOTA", "-1"),
        ("ART_GATEWAY_MONTHLY_CAP_USD", "lots"),
        ("ART_GATEWAY_MONTHLY_CAP_USD", "NaN"),
        ("ART_GATEWAY_COST_PER_RECORD_USD", "0"),
        ("ART_GATEWAY_COST_PER_RECORD_USD", "-0.01"),
    ],
)
def test_an_unusable_value_is_a_config_error_not_a_silent_default(name, value):
    with pytest.raises(ConfigError):
        Config.from_env({name: value})
