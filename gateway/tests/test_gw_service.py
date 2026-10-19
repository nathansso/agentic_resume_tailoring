"""The request pipeline against fake GitHub, Bright Data and KV."""
import json
import logging

import pytest

from artie_gateway.service import Gateway, Request
from artie_gateway.store import InMemoryStore, StoreUnavailable
from artie_gateway.transport import HttpResult

from gw_fakes import PROFILE_URL, RECORD, SECRET_KEY, TOKEN, make_config, post


def code(response):
    return response.body["error"]["code"]


# --- success -----------------------------------------------------------------------------

def test_a_valid_token_and_url_return_the_bright_data_record(gateway, network):
    response = gateway.handle(post())
    assert response.status == 200
    assert response.body == RECORD
    assert response.headers["Cache-Control"] == "no-store"


def test_the_bright_data_request_matches_the_ingestors(gateway, network):
    gateway.handle(post({"url": "linkedin.com/in/Jane-Example-4242/?trk=x"}))
    method, url, headers, body, _ = network.calls_to("api.brightdata.com")[0]
    assert method == "POST"
    assert url.split("?")[0] == "https://api.brightdata.com/datasets/v3/scrape"
    assert "dataset_id=gd_l1viktl72bvl7bjuj0" in url and "format=json" in url and "include_errors=true" in url
    assert headers["Authorization"] == f"Bearer {SECRET_KEY}"
    assert json.loads(body) == [{"url": PROFILE_URL}]  # normalized before it goes out


def test_the_key_is_never_in_the_response(gateway):
    response = gateway.handle(post())
    assert SECRET_KEY not in json.dumps(response.body)
    assert not any(SECRET_KEY in v for v in response.headers.values())


# --- auth --------------------------------------------------------------------------------

@pytest.mark.parametrize("token", [None, "", "short", "has space inside token", "Bearer"])
def test_no_valid_token_is_a_401_and_never_reaches_github(gateway, network, token):
    request = post(token=token)
    if token == "Bearer":
        request.headers["authorization"] = "Bearer"
    response = gateway.handle(request)
    assert response.status == 401 and code(response) == "unauthorized"
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    assert network.calls == []


def test_a_token_github_rejects_is_a_401_and_costs_nothing(gateway, network, store):
    response = gateway.handle(post(token="gho_notARealTokenButWellFormed00"))
    assert response.status == 401
    assert network.calls_to("api.brightdata.com") == []
    assert not [k for k in store.keys() if k.startswith(("quota:", "spend:"))]


def test_scheme_is_case_insensitive(gateway):
    request = post()
    request.headers["authorization"] = f"bearer {TOKEN}"
    assert gateway.handle(request).status == 200


def test_verification_is_cached_so_github_is_asked_once(gateway, network):
    for _ in range(3):
        assert gateway.handle(post()).status == 200
    assert len(network.calls_to("api.github.com")) == 1


def test_verification_cache_expires(gateway, network, clock):
    gateway.handle(post())
    clock.now += 301
    gateway.handle(post(ip="198.51.100.9"))
    assert len(network.calls_to("api.github.com")) == 2


def test_a_rejected_token_is_remembered_briefly(gateway, network):
    bad = "gho_notARealTokenButWellFormed00"
    gateway.handle(post(token=bad))
    gateway.handle(post(token=bad))
    assert len(network.calls_to("api.github.com")) == 1


def test_the_raw_token_is_never_stored(gateway, store):
    gateway.handle(post())
    assert all(TOKEN not in k for k in store.keys())
    assert all(TOKEN not in (store.get(k) or "") for k in store.keys())


@pytest.mark.parametrize(
    "failure,status", [("timeout", 504), ("unreachable", 502), ("500", 502)]
)
def test_github_trouble_is_a_502_or_504_without_the_raw_body(gateway, network, failure, status):
    network.github_failure = failure
    response = gateway.handle(post())
    assert response.status == status
    assert "UPSTREAM-LEAK" not in json.dumps(response.body)


# --- request validation ------------------------------------------------------------------

@pytest.mark.parametrize(
    "body",
    [
        {"url": "https://example.com/in/x"},
        {"url": "https://www.linkedin.com/company/acme"},
        {"url": 5},
        {"nope": 1},
        [],
        "just a string",
    ],
)
def test_bad_bodies_and_urls_are_400_and_cost_no_quota(gateway, network, store, body):
    response = gateway.handle(post(body))
    assert response.status == 400
    assert network.calls_to("api.brightdata.com") == []
    assert not [k for k in store.keys() if k.startswith(("quota:", "spend:"))]


def test_malformed_json_is_400(gateway):
    assert gateway.handle(post(raw=b"{not json")).status == 400


def test_oversized_body_is_413(gateway):
    assert gateway.handle(post(raw=b" " * 5000)).status == 413


def test_other_methods_are_405(gateway):
    request = post()
    request.method = "GET"
    response = gateway.handle(request)
    assert response.status == 405 and response.headers["Allow"] == "POST"


# --- per-user daily quota ----------------------------------------------------------------

def test_the_fourth_profile_of_a_day_is_a_429(gateway, network, clock):
    for i in range(3):
        assert gateway.handle(post(ip=f"203.0.113.{i}")).status == 200
    response = gateway.handle(post(ip="203.0.113.9"))
    assert response.status == 429 and code(response) == "quota_exceeded"
    assert int(response.headers["Retry-After"]) > 0
    assert len(network.calls_to("api.brightdata.com")) == 3   # the refused call was never made


def test_the_quota_resets_at_utc_midnight(gateway, clock):
    for i in range(3):
        gateway.handle(post(ip=f"203.0.113.{i}"))
    assert gateway.handle(post(ip="203.0.113.9")).status == 429
    clock.now += 86400
    assert gateway.handle(post(ip="203.0.113.9")).status == 200


def test_quota_is_per_github_user_not_per_token_or_ip(network, store, clock):
    other = "ghp_anotherTokenForTheSameUser0000"
    network.github_users[other] = 1001                      # same GitHub id, a second token
    gateway = Gateway(make_config(ART_GATEWAY_DAILY_QUOTA="2"), store, network, clock)
    assert gateway.handle(post(token=TOKEN, ip="203.0.113.1")).status == 200
    assert gateway.handle(post(token=other, ip="203.0.113.2")).status == 200
    assert gateway.handle(post(token=TOKEN, ip="203.0.113.3")).status == 429


def test_one_users_quota_does_not_touch_anothers(network, store, clock):
    network.github_users["gho_secondUserToken0000000000"] = 2002
    gateway = Gateway(make_config(ART_GATEWAY_DAILY_QUOTA="1"), store, network, clock)
    assert gateway.handle(post(ip="203.0.113.1")).status == 200
    assert gateway.handle(post(ip="203.0.113.2")).status == 429
    assert gateway.handle(post(token="gho_secondUserToken0000000000", ip="203.0.113.3")).status == 200


def test_the_daily_quota_is_configurable(network, store, clock):
    gateway = Gateway(make_config(ART_GATEWAY_DAILY_QUOTA="1"), store, network, clock)
    assert gateway.handle(post(ip="203.0.113.1")).status == 200
    assert gateway.handle(post(ip="203.0.113.2")).status == 429


def test_a_failed_fetch_hands_the_users_quota_back(gateway, network):
    network.brightdata_failure = "timeout"
    for i in range(5):
        assert gateway.handle(post(ip=f"203.0.113.{i}")).status == 504
    network.brightdata_failure = None
    assert gateway.handle(post(ip="203.0.113.50")).status == 200


# --- per-IP rate limit -------------------------------------------------------------------

def test_the_eleventh_request_a_minute_from_one_ip_is_a_429(network, store, clock):
    gateway = Gateway(make_config(ART_GATEWAY_DAILY_QUOTA="100"), store, network, clock)
    statuses = [gateway.handle(post(ip="203.0.113.7")).status for _ in range(11)]
    assert statuses[:10] == [200] * 10 and statuses[10] == 429
    last = gateway.handle(post(ip="203.0.113.7"))
    assert code(last) == "rate_limited" and int(last.headers["Retry-After"]) >= 1
    assert gateway.handle(post(ip="203.0.113.8")).status == 200   # another address is unaffected


def test_the_ip_limit_applies_before_github_is_asked(network, store, clock):
    gateway = Gateway(make_config(ART_GATEWAY_IP_PER_MINUTE="2"), store, network, clock)
    for _ in range(5):
        gateway.handle(post(token=None, ip="203.0.113.7"))
    assert network.calls == []
    assert gateway.handle(post(token=None, ip="203.0.113.7")).status == 429


def test_the_ip_window_rolls_over(network, store, clock):
    gateway = Gateway(make_config(ART_GATEWAY_IP_PER_MINUTE="1", ART_GATEWAY_DAILY_QUOTA="100"), store, network, clock)
    assert gateway.handle(post()).status == 200
    assert gateway.handle(post()).status == 429
    clock.now += 60
    assert gateway.handle(post()).status == 200


def test_raw_ip_addresses_are_not_stored(gateway, store):
    gateway.handle(post(ip="203.0.113.7"))
    assert all("203.0.113.7" not in k for k in store.keys())


# --- global monthly spend cap ------------------------------------------------------------

def make_capped(network, store, clock, cap, cost):
    return Gateway(
        make_config(
            ART_GATEWAY_MONTHLY_CAP_USD=cap,
            ART_GATEWAY_COST_PER_RECORD_USD=cost,
            ART_GATEWAY_DAILY_QUOTA="100",
            ART_GATEWAY_IP_PER_MINUTE="100",
        ),
        store, network, clock,
    )


def test_calls_times_cost_over_the_cap_turns_the_endpoint_off(network, store, clock):
    gateway = make_capped(network, store, clock, cap="0.10", cost="0.05")    # two records
    assert gateway.handle(post()).status == 200
    assert gateway.handle(post()).status == 200
    response = gateway.handle(post())
    assert response.status == 503 and code(response) == "spend_cap_reached"
    assert len(network.calls_to("api.brightdata.com")) == 2


def test_a_cap_refusal_does_not_cost_the_user_a_daily_slot(network, store, clock):
    gateway = make_capped(network, store, clock, cap="0.05", cost="0.05")
    gateway.handle(post())
    for _ in range(4):
        assert gateway.handle(post()).status == 503
    key = next(k for k in store.keys() if k.startswith("quota:"))
    assert store.get(key) == "1"


def test_the_cap_resets_with_the_month(network, store, clock):
    gateway = make_capped(network, store, clock, cap="0.05", cost="0.05")
    assert gateway.handle(post()).status == 200
    assert gateway.handle(post()).status == 503
    clock.now += 31 * 86400                       # 2025-10-09 + 31 days is in November
    assert gateway.handle(post()).status == 200


def test_a_failed_fetch_still_counts_toward_spend(network, store, clock):
    gateway = make_capped(network, store, clock, cap="0.05", cost="0.05")
    network.brightdata_failure = "timeout"
    assert gateway.handle(post()).status == 504
    network.brightdata_failure = None
    assert gateway.handle(post()).status == 503   # the cap is conservative


# --- Bright Data failures ----------------------------------------------------------------

LEAK = b'{"error": "internal stack UPSTREAM-LEAK key=abc"}'


@pytest.mark.parametrize(
    "response,status",
    [
        (HttpResult(500, LEAK), 502),
        (HttpResult(429, LEAK), 502),
        (HttpResult(401, LEAK), 502),
        (HttpResult(200, b"<html>not json UPSTREAM-LEAK"), 502),
        (HttpResult(200, b'{"error": "dead_page UPSTREAM-LEAK"}'), 502),
        (HttpResult(200, b'[{"warning": "UPSTREAM-LEAK"}]'), 502),
        (HttpResult(200, b"[]"), 502),
        (HttpResult(200, b"{}"), 502),
        (HttpResult(200, b'"a string"'), 502),
        (HttpResult(202, b'{"status": "running"}'), 504),          # 202 with no snapshot id
    ],
)
def test_bright_data_failures_map_to_502_or_504_with_a_fixed_message(gateway, network, response, status):
    network.brightdata_response = response
    result = gateway.handle(post())
    assert result.status == status
    assert "UPSTREAM-LEAK" not in json.dumps(result.body)
    assert set(result.body) == {"error"} and set(result.body["error"]) == {"code", "message"}


def test_bright_data_timeout_is_a_504_and_unreachable_a_502(gateway, network):
    network.brightdata_failure = "timeout"
    assert gateway.handle(post()).status == 504
    network.brightdata_failure = "unreachable"
    assert gateway.handle(post(ip="203.0.113.99")).status == 502


def test_a_single_object_response_is_accepted(gateway, network):
    network.brightdata_response = HttpResult(200, json.dumps(RECORD).encode())
    assert gateway.handle(post()).body == RECORD


# --- availability ------------------------------------------------------------------------

def test_without_a_bright_data_key_or_store_the_answer_is_503_not_configured(network, clock):
    keyless = Gateway(make_config(BRIGHTDATA_API_KEY=""), InMemoryStore(clock), network, clock)
    storeless = Gateway(make_config(), None, network, clock)
    for gateway in (keyless, storeless):
        response = gateway.handle(post())
        assert response.status == 503 and code(response) == "not_configured"
    assert network.calls == []


def test_a_down_store_fails_closed_with_503(network, clock):
    class Broken(InMemoryStore):
        def incr(self, key, ttl_seconds):
            raise StoreUnavailable()

    response = Gateway(make_config(), Broken(clock), network, clock).handle(post())
    assert response.status == 503 and code(response) == "unavailable"
    assert network.calls == []


def test_an_unexpected_error_is_a_generic_500(network, store, clock):
    class Boom(InMemoryStore):
        def incr(self, key, ttl_seconds):
            raise RuntimeError("secret detail the caller must not see")

    response = Gateway(make_config(), Boom(clock), network, clock).handle(post())
    assert response.status == 500
    assert "secret detail" not in json.dumps(response.body)


# -- the log-only reason for a Bright Data failure ------------------------------

@pytest.mark.parametrize("response, reason", [
    (HttpResult(401, b'{"error": "Invalid token"}'), "upstream_http_401"),
    (HttpResult(403, b'{"error": "Account is not active"}'), "upstream_http_403"),
    (HttpResult(200, b"not json"), "upstream_not_json"),
    (HttpResult(200, b"[]"), "upstream_empty"),
    (HttpResult(200, json.dumps([{"error": "Dead page", "error_code": "dead_page"}]).encode()), "record_error:dead_page"),
    (HttpResult(200, json.dumps([{"warning": "Partial", "warning_code": "Has spaces!"}]).encode()), "record_warning:unknown"),
])
def test_a_bright_data_failure_logs_why_without_its_body(gateway, network, caplog, response, reason):
    caplog.set_level(logging.INFO)
    network.brightdata_response = response
    got = gateway.handle(post())
    assert got.status == 502 and got.body["error"]["code"] == "upstream_error"
    assert reason not in json.dumps(got.body)                       # log-only: never sent to the caller
    line = [r.getMessage() for r in caplog.records if r.name == "artie_gateway"][-1]
    assert line.endswith(f"reason={reason}")
    assert "Invalid token" not in line and "not active" not in line and "Dead page" not in line


# -- a scrape that outlives Bright Data's synchronous window (202 + snapshot) ---------

ACCEPTED = HttpResult(202, b'{"snapshot_id": "s_abc123", "message": "still running"}')


def test_a_202_is_polled_until_ready_and_the_snapshot_is_returned(gateway, network, clock):
    network.brightdata_response = ACCEPTED
    network.progress = ["running", "running", "ready"]
    network.snapshot_building = 1
    started = clock.now
    result = gateway.handle(post())
    assert result.status == 200 and result.body == RECORD
    urls = [c[1] for c in network.calls_to("api.brightdata.com")]
    assert sum("/progress/s_abc123" in u for u in urls) == 3
    assert sum("/snapshot/s_abc123?format=json" in u for u in urls) == 2       # one "building", then the file
    assert all(c[3] is None for c in network.calls_to("api.brightdata.com")[1:])  # polls send no body
    assert clock.now - started == 20                       # three progress waits and one "building" wait


def test_a_failed_snapshot_is_a_502_and_refunds_the_quota(gateway, network, store, caplog):
    caplog.set_level(logging.INFO)
    network.brightdata_response = ACCEPTED
    network.progress = ["running", "failed"]
    result = gateway.handle(post())
    assert result.status == 502 and result.body["error"]["code"] == "upstream_error"
    assert caplog.records[-1].getMessage().endswith("reason=snapshot_failed")
    for _ in range(3):                                     # the quota was refunded each time
        network.brightdata_response = None
        assert gateway.handle(post()).status == 200


def test_a_snapshot_that_never_finishes_times_out_within_the_budget(network, store, clock, caplog):
    caplog.set_level(logging.INFO)
    gateway = Gateway(make_config(ART_GATEWAY_UPSTREAM_TIMEOUT="60"), store, network, clock, sleep=clock.advance)
    network.brightdata_response = ACCEPTED
    network.progress = ["running"]
    started = clock.now
    result = gateway.handle(post())
    assert result.status == 504 and result.body["error"]["code"] == "upstream_timeout"
    assert caplog.records[-1].getMessage().endswith("reason=snapshot_timeout")
    assert clock.now - started <= 60                       # never waits past the budget
    assert len([c for c in network.calls if "/progress/" in c[1]]) <= 12


def test_a_202_without_a_usable_snapshot_id_is_a_504(gateway, network, caplog):
    caplog.set_level(logging.INFO)
    for body in (b'{"status": "running"}', b'{"snapshot_id": "../../etc"}', b"not json"):
        network.calls.clear()
        network.brightdata_response = HttpResult(202, body)
        assert gateway.handle(post()).status == 504
        assert caplog.records[-1].getMessage().endswith("reason=snapshot_no_id")
        assert not [c for c in network.calls if "/progress/" in c[1]]     # nothing is polled
