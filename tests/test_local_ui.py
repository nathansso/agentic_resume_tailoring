"""`art ui` (issue #204): local mode, its guards, the SSE change feed, the launcher."""

import json
import threading

import pytest
from sqlmodel import Session

from database.models import User

# Build the module-level `web.app.app` now, outside local mode: other test files
# drive that shared instance on the default `testserver` host.
import web.app  # noqa: F401,E402

LOCAL = "http://127.0.0.1:8765"


def _content(*bullets):
    return {"experiences": [{"title": "Data Science Intern", "company": "IDX Exchange",
                             "bullets": list(bullets)}],
            "projects": [{"name": "Next-Item Recommendation",
                          "bullets": ["Compared XGBoost with a GNN"]}],
            "skills_ranked": [{"name": "Python"}]}


@pytest.fixture()
def job(isolated_engine, monkeypatch):
    """A user with one tailored job; engines patched for the web layer."""
    import database.db as db_module
    import web.routers.jobs_router as jobs_router_module
    from conftest import _seed_user_and_skill
    from test_job_card import _seed_job_and_result

    monkeypatch.setattr(db_module, "engine", isolated_engine)
    monkeypatch.setattr(jobs_router_module, "engine", isolated_engine)
    user = _seed_user_and_skill(isolated_engine)
    job_id, result_id = _seed_job_and_result(
        isolated_engine, user.user_id, content=_content("Built an ensemble"))
    return user.user_id, job_id, result_id


@pytest.fixture()
def local_client(monkeypatch):
    """A TestClient for an app built in local mode, bound to `uid`."""
    from fastapi.testclient import TestClient

    def _make(uid, base_url=LOCAL):
        monkeypatch.setenv("ART_LOCAL_UI", "1")
        monkeypatch.setenv("ART_LOCAL_UI_USER_ID", str(uid))
        from web.app import create_app
        return TestClient(create_app(), base_url=base_url)

    return _make


def _host_commit(engine, uid, jid, result_id, bullet):
    """What a host run does: change the result, then commit a `host` node."""
    from harness import tree
    from database.models import UserJobResult
    with Session(engine) as s:
        r = s.get(UserJobResult, result_id)
        r.tailored_resume_content = _content(bullet)
        s.add(r)
        s.commit()
    return tree.commit_node(uid, jid, content=_content(bullet), source="host")


def _events(body: str):
    out = []
    for block in body.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines()
                     if not line.startswith(":") and ": " in line)
        if lines.get("event") == "tree":
            out.append({"id": int(lines["id"]), **json.loads(lines["data"])})
    return out


@pytest.fixture()
def short_stream(monkeypatch):
    import web.routers.jobs_router as jr
    monkeypatch.setattr(jr, "EVENTS_POLL_SECONDS", 0.02)
    monkeypatch.setattr(jr, "EVENTS_MAX_SECONDS", 0.2)
    return jr


# ── local mode auth ──────────────────────────────────────────────────────────

def test_a_cookieless_request_gets_the_local_user_only_in_local_mode(
        job, local_client, monkeypatch):
    from fastapi.testclient import TestClient
    from web.app import create_app

    uid, _, _ = job
    me = local_client(uid).get("/api/auth/me")
    assert me.status_code == 200 and me.json()["id"] == str(uid)

    monkeypatch.delenv("ART_LOCAL_UI")
    assert TestClient(create_app()).get("/api/auth/me").status_code == 401


def test_capabilities_report_no_login_in_local_mode(job, local_client, monkeypatch):
    from fastapi.testclient import TestClient
    from web.app import create_app

    caps = local_client(job[0]).get("/api/auth/capabilities").json()
    assert caps == {"password_reset_enabled": False, "auth_mode": "none"}
    monkeypatch.delenv("ART_LOCAL_UI")
    assert TestClient(create_app()).get("/api/auth/capabilities").json()["auth_mode"] != "none"


def test_quotas_do_not_apply_in_local_mode(job, isolated_engine, monkeypatch):
    import web.routers.dependencies as deps
    uid, _, _ = job
    with Session(isolated_engine) as s:
        for _ in range(3):
            deps._increment(uid, s, "ai")
        assert not deps._has_quota(s, uid, "x@example.com", "ai", 2)
        monkeypatch.setenv("ART_LOCAL_UI", "1")
        assert deps._has_quota(s, uid, "x@example.com", "ai", 2)


# ── guards ───────────────────────────────────────────────────────────────────

def test_a_foreign_host_is_refused(job, local_client):
    client = local_client(job[0], base_url="http://evil.example:8765")
    assert client.get("/api/auth/me").status_code == 400


def test_a_cross_origin_write_is_refused_and_a_same_origin_one_is_not(job, local_client):
    uid, jid, _ = job
    client = local_client(uid)
    body = {"section_order": ["projects", "experience"]}
    foreign = client.put(f"/api/jobs/{jid}/layout", json=body,
                         headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403
    own = client.put(f"/api/jobs/{jid}/layout", json=body, headers={"Origin": LOCAL})
    assert own.status_code == 200
    # A cross-origin GET is harmless (the browser won't let the page read it).
    assert client.get("/api/auth/me", headers={"Origin": "https://evil.example"}).status_code == 200


def test_origin_rule_is_pure():
    from web.local_mode import origin_allowed
    assert origin_allowed("GET", "https://evil.example", "127.0.0.1:8765")
    assert origin_allowed("POST", None, "127.0.0.1:8765")
    assert origin_allowed("POST", "http://127.0.0.1:8765", "127.0.0.1:8765")
    assert not origin_allowed("POST", "http://127.0.0.1:9999", "127.0.0.1:8765")
    assert not origin_allowed("DELETE", "null", "127.0.0.1:8765")


# ── the change feed ──────────────────────────────────────────────────────────

def test_the_feed_yields_a_host_commit_made_after_the_cursor(
        job, isolated_engine, local_client, short_stream):
    from harness import tree
    uid, jid, rid = job
    first = _host_commit(isolated_engine, uid, jid, rid, "Built a tuned ensemble")
    cursor = tree.get_head(uid, jid)["cursor"]
    second = _host_commit(isolated_engine, uid, jid, rid, "Built a stacked ensemble")

    body = local_client(uid).get(f"/api/jobs/{jid}/events?since={cursor}").text
    events = _events(body)
    assert [e["node_id"] for e in events] == [second["node_id"]]
    assert events[0]["source"] == "host" and events[0]["kind"] == "commit"
    assert events[0]["id"] == events[0]["event_id"] > cursor
    assert first["node_id"] not in body


def test_the_feed_resumes_from_last_event_id(job, isolated_engine, local_client, short_stream):
    uid, jid, rid = job
    _host_commit(isolated_engine, uid, jid, rid, "one")
    client = local_client(uid)
    everything = _events(client.get(f"/api/jobs/{jid}/events?since=0").text)
    _host_commit(isolated_engine, uid, jid, rid, "two")
    resumed = _events(client.get(f"/api/jobs/{jid}/events",
                                 headers={"Last-Event-ID": str(everything[-1]["id"])}).text)
    assert len(resumed) == 1 and resumed[0]["id"] > everything[-1]["id"]


def test_a_fresh_stream_starts_at_now_and_sees_a_commit_made_while_open(
        job, isolated_engine, local_client, short_stream, monkeypatch):
    uid, jid, rid = job
    _host_commit(isolated_engine, uid, jid, rid, "before the editor opened")
    monkeypatch.setattr(short_stream, "EVENTS_MAX_SECONDS", 2.0)
    committed = {}
    timer = threading.Timer(0.3, lambda: committed.update(
        _host_commit(isolated_engine, uid, jid, rid, "while the editor is open")))
    timer.start()
    try:
        body = local_client(uid).get(f"/api/jobs/{jid}/events").text
    finally:
        timer.join()
    events = _events(body)
    assert [e["node_id"] for e in events] == [committed["node_id"]]


def test_the_feed_is_scoped_to_the_owner(job, isolated_engine, local_client, short_stream):
    uid, jid, _ = job
    with Session(isolated_engine) as s:
        other = User(name="Other", email="other@example.com")
        s.add(other)
        s.commit()
        s.refresh(other)
    assert local_client(other.user_id).get(f"/api/jobs/{jid}/events").status_code == 404


# ── editor edits reach the host ──────────────────────────────────────────────

def test_a_drag_in_local_mode_appears_in_the_hosts_next_turn(
        job, isolated_engine, local_client, short_stream):
    """PUT /layout is what a drag sends; get_head is what the host's hook reads."""
    from harness import tree
    uid, jid, rid = job
    _host_commit(isolated_engine, uid, jid, rid, "Built an ensemble")
    cursor = tree.get_head(uid, jid)["cursor"]
    client = local_client(uid)
    body = {"section_order": ["projects", "experience"]}
    assert client.put(f"/api/jobs/{jid}/layout", json=body,
                      headers={"Origin": LOCAL}).status_code == 200

    head = tree.get_head(uid, jid, since_event=cursor)
    assert [e["note"] for e in head["editor_edits"]] == ["layout"]
    assert head["head"]["layout_overrides"] == body
    # The editor sees its own commit on the feed, marked so it can ignore it.
    events = _events(client.get(f"/api/jobs/{jid}/events?since={cursor}").text)
    assert [e["source"] for e in events] == ["editor"]


# ── launcher ─────────────────────────────────────────────────────────────────

def test_the_launcher_refuses_without_a_built_editor(tmp_path, capsys, monkeypatch):
    from web import local_ui
    monkeypatch.delenv("ART_LOCAL_UI", raising=False)
    assert "npm --prefix web/frontend" in local_ui.missing_static(tmp_path)
    assert local_ui.main(["--no-open"], static_dir=tmp_path) == 2
    assert "has not been built" in capsys.readouterr().err
    # It refused before touching the environment it would otherwise pin.
    import os
    assert "ART_LOCAL_UI" not in os.environ

    (tmp_path / "index.html").write_text("<html></html>")
    assert local_ui.missing_static(tmp_path) is None


def test_the_launcher_url_carries_the_job():
    from web.local_ui import editor_url
    assert editor_url(8765, "abc") == "http://127.0.0.1:8765/?job=abc"
    assert editor_url(9000) == "http://127.0.0.1:9000/"
