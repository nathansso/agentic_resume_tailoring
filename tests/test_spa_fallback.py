"""SPA fallback tests — direct loads of client routes must serve index.html.

Regression coverage for email links (Supabase confirmation → /login,
password recovery → /reset-password) 404ing because StaticFiles(html=True)
only serves index.html at the root path.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"

pytestmark = pytest.mark.skipif(
    not (STATIC_DIR / "index.html").exists(),
    reason="frontend build (web/static) not present",
)


@pytest.fixture()
def client():
    from web.app import create_app
    return TestClient(create_app())


@pytest.mark.parametrize("route", ["/login", "/register", "/reset-password", "/forgot-password"])
def test_client_routes_serve_index_html(client, route):
    resp = client.get(route)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_root_serves_index_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_html_is_never_cached_without_revalidation(client):
    # Stale cached HTML keeps users on an old bundle whose API payloads the
    # backend rejects (422 login storms). no-cache forces revalidation.
    for route in ("/", "/login"):
        assert client.get(route).headers.get("cache-control") == "no-cache"


def test_unknown_api_route_still_404s(client):
    resp = client.get("/api/definitely-not-a-route")
    assert resp.status_code == 404


def test_path_traversal_rejected(client):
    resp = client.get("/..%2f..%2fconfig.py")
    # Must not leak files outside web/static — the fallback serves index.html instead.
    assert "text/html" in resp.headers["content-type"]
    assert "import" not in resp.text[:200]
