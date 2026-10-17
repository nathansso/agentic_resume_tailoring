"""Privacy: no request or response body, and no token, in any log, and nothing
stored beyond counters."""
import json
import logging

from artie_gateway.service import Gateway
from artie_gateway.store import InMemoryStore
from artie_gateway.transport import HttpResult

from gw_fakes import RECORD, SECRET_KEY, SLUG, TOKEN, make_config, post


def run_everything(network, store, clock):
    """Drive success, every refusal and every upstream failure through one gateway."""
    gateway = Gateway(make_config(ART_GATEWAY_DAILY_QUOTA="50", ART_GATEWAY_IP_PER_MINUTE="500"), store, network, clock)
    gateway.handle(post())                                                    # 200
    gateway.handle(post(token=None))                                          # 401
    gateway.handle(post({"url": "https://example.com/in/sentinel-leak"}))     # 400
    gateway.handle(post(raw=b'{"url": "SENTINEL-BAD-JSON'))                   # 400
    network.github_failure = "timeout"
    gateway.handle(post(token="gho_anotherFakeTokenWithSecrets1234"))         # 504
    network.github_failure = None
    for response in (HttpResult(500, b"SENTINEL-UPSTREAM-BODY"), HttpResult(200, b"SENTINEL-NOT-JSON")):
        network.brightdata_response = response
        gateway.handle(post())                                                # 502
    network.brightdata_failure = "timeout"
    gateway.handle(post())                                                    # 504
    return gateway


def test_no_log_line_carries_a_body_a_token_or_the_key(network, store, clock, caplog, capsys):
    caplog.set_level(logging.DEBUG)                       # capture everything, including DEBUG
    run_everything(network, store, clock)

    logged = caplog.text
    out = capsys.readouterr()
    for stream in (logged, out.out, out.err):
        assert TOKEN not in stream
        assert "gho_anotherFakeTokenWithSecrets1234" not in stream
        assert SECRET_KEY not in stream
        assert SLUG not in stream and "linkedin.com/in" not in stream            # the request body
        assert "SENTINEL" not in stream                                          # upstream and request text
        assert "Jane Example" not in stream and RECORD["about"] not in stream    # the profile record
    assert "outcome=ok status=200" in logged                                     # it does log something


def test_each_request_logs_only_fixed_fields(network, store, clock, caplog):
    caplog.set_level(logging.DEBUG)
    run_everything(network, store, clock)
    records = [r for r in caplog.records if r.name == "artie_gateway"]
    assert len(records) == 8
    for record in records:
        assert record.args and len(record.args) == 3          # outcome, status, ms: nothing else
        assert record.getMessage().startswith("linkedin request outcome=")


def test_an_unexpected_exception_logs_its_type_not_its_message(network, clock, caplog):
    class Boom(InMemoryStore):
        def incr(self, key, ttl_seconds):
            raise RuntimeError(f"leaky message with {TOKEN}")

    caplog.set_level(logging.DEBUG)
    Gateway(make_config(), Boom(clock), network, clock).handle(post())
    assert "RuntimeError" in caplog.text
    assert TOKEN not in caplog.text and "leaky" not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)    # no traceback attached


def test_the_store_holds_counters_and_hashes_only(network, store, clock):
    run_everything(network, store, clock)
    keys = store.keys()
    assert keys
    assert all(k.split(":")[0] in {"ip", "quota", "spend", "gh"} for k in keys)
    blob = json.dumps({k: store.get(k) for k in keys})
    for forbidden in (TOKEN, SLUG, "Jane", "linkedin", SECRET_KEY, "203.0.113.7", "SENTINEL"):
        assert forbidden not in blob
    # Values are small integers or a GitHub user id, never text from a profile.
    assert all(store.get(k).isdigit() or store.get(k) == "invalid" for k in keys)


def test_every_stored_key_expires(network, store, clock):
    run_everything(network, store, clock)
    clock.now += 46 * 86400
    assert store.keys() == []


def test_the_response_is_not_retained_by_the_gateway(network, store, clock):
    gateway = run_everything(network, store, clock)
    # Nothing on the gateway object keeps a profile: only config, store, clients.
    held = json.dumps({k: repr(v) for k, v in vars(gateway).items()})
    assert "Jane Example" not in held and "SENTINEL" not in held
