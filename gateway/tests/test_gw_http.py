"""The HTTP adapter, over a real localhost socket with the fake network behind it."""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from artie_gateway.adapter import GatewayHandler, gateway_from_env
from artie_gateway.devserver import DevHandler
from artie_gateway.service import Gateway

from gw_fakes import RECORD, TOKEN, make_config


@pytest.fixture
def server(network, store, clock):
    gateway = Gateway(make_config(), store, network, clock)

    class Handler(DevHandler):
        get_gateway = staticmethod(lambda: gateway)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def call(port, method="POST", path="/v1/linkedin", body=None, token=TOKEN, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    hdrs = dict(headers or {})
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    payload = json.dumps(body if body is not None else {"url": "https://www.linkedin.com/in/jane-example"})
    conn.request(method, path, body=payload if method == "POST" else None, headers=hdrs)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, dict(response.getheaders()), json.loads(data)


def test_post_returns_the_record_as_json(server):
    status, headers, body = call(server)
    assert status == 200 and body == RECORD
    assert headers["Content-Type"] == "application/json"


def test_the_api_prefixed_path_works_too(server):
    assert call(server, path="/api/v1/linkedin")[0] == 200


def test_error_bodies_and_headers_pass_through(server):
    status, headers, body = call(server, token=None)
    assert status == 401
    assert body["error"]["code"] == "unauthorized" and "WWW-Authenticate" in headers


def test_get_is_405_and_unknown_paths_are_404(server):
    assert call(server, method="GET")[0] == 405
    assert call(server, path="/elsewhere")[0] == 404


def test_the_client_ip_comes_from_the_platform_header(network, store, clock):
    gateway = Gateway(make_config(ART_GATEWAY_IP_PER_MINUTE="1", ART_GATEWAY_DAILY_QUOTA="50"), store, network, clock)

    class Handler(DevHandler):
        get_gateway = staticmethod(lambda: gateway)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        # All three come from 127.0.0.1; only the forwarded address tells them apart.
        assert call(port, headers={"x-vercel-forwarded-for": "198.51.100.1"})[0] == 200
        assert call(port, headers={"x-vercel-forwarded-for": "198.51.100.1, 10.0.0.1"})[0] == 429
        assert call(port, headers={"x-vercel-forwarded-for": "198.51.100.2"})[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_handler_class_is_what_vercel_looks_for():
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "api" / "v1" / "linkedin.py"
    spec = importlib.util.spec_from_file_location("linkedin_function", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert issubclass(module.handler, GatewayHandler)


def test_from_env_builds_a_working_unconfigured_gateway_instead_of_crashing():
    gateway = gateway_from_env({"ART_GATEWAY_DAILY_QUOTA": "oops"})
    assert gateway.store is None


def test_from_env_uses_kv_when_present_and_memory_only_when_allowed():
    from artie_gateway.store import InMemoryStore, UpstashStore

    kv = gateway_from_env({"BRIGHTDATA_API_KEY": "k", "KV_REST_API_URL": "https://kv.example", "KV_REST_API_TOKEN": "t"})
    assert isinstance(kv.store, UpstashStore)
    dev = gateway_from_env({"BRIGHTDATA_API_KEY": "k", "ART_GATEWAY_ALLOW_MEMORY_STORE": "1"})
    assert isinstance(dev.store, InMemoryStore)
    assert gateway_from_env({"BRIGHTDATA_API_KEY": "k"}).store is None
