"""Which job? (issue #288): the last-touched signal, the ranked lookup, the editor-switch line.

The agent works out which job the user means and confirms it; nothing picks one silently.
`last_touched_at` is set by every host write to a job and by the user opening it in `artie ui`;
`list_jobs(query=...)` ranks a company or title match first, then recency, and says why; the
prompt hook adds one line when the user switched jobs in the editor.
"""

from datetime import timedelta
from uuid import UUID

import pytest
from sqlmodel import Session

from database.clock import utc_now
from database.models import JobDescription, User
from harness.contract import invoke

# Build the module-level `web.app.app` now, outside local mode: other test files drive that
# shared instance on the default `testserver` host (as tests/test_local_ui.py does).
import web.app  # noqa: F401,E402

# The executor's seeded benchmark job, with a one-line measurer (no LaTeX engine needed).
from test_executor import PROJ, env  # noqa: F401  (a fixture)

LOCAL = "http://127.0.0.1:8765"


def _user(engine, name="U"):
    with Session(engine) as s:
        user = User(name=name, email=f"{name.lower()}@example.com")
        s.add(user)
        s.commit()
        return user.user_id


def _job(engine, uid, title, company, *, created=None, touched=None, opened=None):
    with Session(engine) as s:
        job = JobDescription(user_id=uid, title=title, company=company, description="",
                             created_at=created or utc_now(), last_touched_at=touched,
                             last_opened_at=opened)
        s.add(job)
        s.commit()
        return str(job.job_id)


def _stamps(engine, job_id):
    with Session(engine) as s:
        job = s.get(JobDescription, UUID(job_id))
        return job.last_touched_at, job.last_opened_at


def _reset(engine, job_id):
    with Session(engine) as s:
        job = s.get(JobDescription, UUID(job_id))
        job.last_touched_at = job.last_opened_at = None
        s.add(job)
        s.commit()


# ── the signal: every host write path ────────────────────────────────────────

def test_open_job_touches_the_job_it_opens(isolated_engine):
    uid = _user(isolated_engine)
    out = invoke("open_job", uid, {"jd_text": "Data Scientist\nPython and SQL.",
                                   "metadata": {"title": "Data Scientist", "company": "Zendar"}})
    touched, opened = _stamps(isolated_engine, out["job_id"])
    assert touched is not None and opened is None        # a host write is not an editor open
    _reset(isolated_engine, out["job_id"])
    invoke("open_job", uid, {"job_id": out["job_id"], "metadata": {"status": "applied"}})
    assert _stamps(isolated_engine, out["job_id"])[0] is not None


def test_execute_plan_touches_on_a_commit_only_and_the_other_writes_always(isolated_engine, env):
    uid, job_id, _ = env
    _reset(isolated_engine, job_id)

    dry = invoke("execute_plan", uid, {"program": {"job_id": job_id, "parent": None},
                                       "dry_run": True})
    assert dry["error"] is None and not dry["committed"]
    assert _stamps(isolated_engine, job_id)[0] is None          # a dry run writes nothing

    first = invoke("execute_plan", uid, {"program": {"job_id": job_id, "parent": None}})
    assert first["committed"]
    assert _stamps(isolated_engine, job_id)[0] is not None

    _reset(isolated_engine, job_id)
    plan = {"job_id": job_id, "parent": first["node_id"], "nodes": [
        {"id": "d", "op": "delete", "item_key": PROJ, "because": "user:too junior"}]}
    saved = invoke("execute_plan", uid, {"program": plan, "dry_run": True})
    assert _stamps(isolated_engine, job_id)[0] is None
    patched = invoke("patch_plan", uid, {"program_id": saved["program_id"], "edits": [],
                                         "plan_token": saved["plan_token"]})   # token-only (#393)
    assert patched["error"] is None and patched["committed"]
    assert _stamps(isolated_engine, job_id)[0] is not None

    _reset(isolated_engine, job_id)
    back = invoke("checkout", uid, {"job_id": job_id, "node_id": first["node_id"]})
    assert back["error"] is None
    assert _stamps(isolated_engine, job_id)[0] is not None

    _reset(isolated_engine, job_id)
    rendered = invoke("render", uid, {"job_id": job_id, "format": "tex"})
    assert rendered["error"] is None
    assert _stamps(isolated_engine, job_id)[0] is not None

    # Reads leave it alone, and so does a write that failed.
    _reset(isolated_engine, job_id)
    invoke("get_head", uid, {"job_id": job_id})
    invoke("history", uid, {"job_id": job_id})
    invoke("checkout", uid, {"job_id": job_id, "node_id": "00000000-0000-0000-0000-000000000000"})
    assert _stamps(isolated_engine, job_id) == (None, None)


def test_touch_ignores_another_users_job_and_bad_ids(isolated_engine):
    from harness import jobs

    alice, bob = _user(isolated_engine, "Alice"), _user(isolated_engine, "Bob")
    jid = _job(isolated_engine, alice, "Data Scientist", "Zendar")
    assert not jobs.touch(bob, jid) and not jobs.touch(alice, "not-a-uuid")
    assert _stamps(isolated_engine, jid) == (None, None)
    assert jobs.touch(alice, jid, opened=True)
    touched, opened = _stamps(isolated_engine, jid)
    assert touched is not None and touched == opened


# ── the signal: opening a job in artie ui ────────────────────────────────────

@pytest.fixture()
def ui_job(isolated_engine, monkeypatch):
    import web.routers.jobs_router as jobs_router_module
    from conftest import _seed_user_and_skill

    monkeypatch.setattr(jobs_router_module, "engine", isolated_engine)
    uid = _seed_user_and_skill(isolated_engine).user_id
    return uid, _job(isolated_engine, uid, "Data Scientist", "Zendar")


def _client(uid, monkeypatch, local=True):
    from fastapi.testclient import TestClient

    if local:
        monkeypatch.setenv("ART_LOCAL_UI", "1")
        monkeypatch.setenv("ART_LOCAL_UI_USER_ID", str(uid))
    else:
        monkeypatch.delenv("ART_LOCAL_UI", raising=False)
    from web.app import create_app
    return TestClient(create_app(), base_url=LOCAL)


def test_opening_a_job_in_artie_ui_touches_it_through_a_same_origin_write(
        isolated_engine, ui_job, monkeypatch):
    uid, jid = ui_job
    client = _client(uid, monkeypatch)

    foreign = client.post(f"/api/jobs/{jid}/open", headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403
    assert _stamps(isolated_engine, jid) == (None, None)

    own = client.post(f"/api/jobs/{jid}/open", headers={"Origin": LOCAL})
    assert own.status_code == 200 and own.json() == {"touched": True}
    touched, opened = _stamps(isolated_engine, jid)
    assert touched is not None and opened == touched

    assert client.post("/api/jobs/00000000-0000-0000-0000-000000000000/open",
                       headers={"Origin": LOCAL}).status_code == 404


def test_the_open_route_exists_only_in_local_mode(isolated_engine, ui_job, monkeypatch):
    uid, jid = ui_job
    client = _client(uid, monkeypatch, local=False)
    assert client.post(f"/api/jobs/{jid}/open").status_code in (401, 404)
    assert _stamps(isolated_engine, jid) == (None, None)


# ── the ranked lookup ────────────────────────────────────────────────────────

@pytest.fixture()
def four_jobs(isolated_engine):
    """Storage order, creation order and recency all disagree with the right answer.

    Inserted Stripe, Never, Acme, Zendar: the Zendar job is stored last, created first and
    touched longest ago, so it ranks first for "zendar" only by matching the company."""
    uid = _user(isolated_engine)
    now = utc_now()
    ids = {
        "stripe": _job(isolated_engine, uid, "ML Engineer", "Stripe",
                       created=now - timedelta(days=1), touched=now - timedelta(seconds=20)),
        "never": _job(isolated_engine, uid, "Research Engineer", "Nimbus",
                      created=now - timedelta(hours=1)),
        "acme": _job(isolated_engine, uid, "Zendar Integration Engineer", "Acme",
                     created=now - timedelta(days=5), touched=now - timedelta(hours=2)),
        "zendar": _job(isolated_engine, uid, "Data Scientist", "Zendar, Inc.",
                       created=now - timedelta(days=30), touched=now - timedelta(days=3)),
    }
    return uid, ids


def _order(out, ids):
    names = {v: k for k, v in ids.items()}
    return [names[j["job_id"]] for j in out["jobs"]]


def test_a_company_match_outranks_a_title_match_and_any_recency(four_jobs):
    uid, ids = four_jobs
    out = invoke("list_jobs", uid, {"query": "revise the resume for the Zendar role"})
    assert _order(out, ids) == ["zendar", "acme", "stripe", "never"]
    assert out["matched"] == 2 and out["query"] == "revise the resume for the Zendar role"
    top, title_hit, recent, never = out["jobs"]
    assert top["score"] == 2.0 and top["matched_words"] == ['"zendar" (company)']
    assert top["reason"] == 'matched "zendar" (company); touched 3 d ago'
    assert title_hit["reason"] == 'matched "zendar" (title); touched 2 h ago'
    assert recent["score"] == 0 and recent["reason"] == "no match; touched just now"
    assert never["reason"] == "no match; never touched" and never["last_touched_at"] is None


def test_matching_ignores_case_punctuation_and_role_words(four_jobs):
    uid, ids = four_jobs
    assert _order(invoke("list_jobs", uid, {"query": "ZENDAR, INC."}), ids)[0] == "zendar"
    # Company and title words add up: Stripe (company) + ML (title) beats a recent title-only hit.
    out = invoke("list_jobs", uid, {"query": "that ml job at stripe"})
    assert _order(out, ids)[0] == "stripe" and out["jobs"][0]["score"] == 3.0
    # "engineering" names "Engineer"; the most recently touched of the three wins the tie.
    out = invoke("list_jobs", uid, {"query": "the engineering one"})
    assert _order(out, ids) == ["stripe", "acme", "never", "zendar"] and out["matched"] == 3


def test_without_a_query_the_most_recently_touched_come_first(four_jobs):
    uid, ids = four_jobs
    out = invoke("list_jobs", uid, {})
    assert _order(out, ids) == ["stripe", "acme", "zendar", "never"]
    assert out["matched"] is None and out["jobs"][0]["score"] is None
    assert out["jobs"][0]["reason"] == "touched just now"
    assert _order(invoke("list_jobs", uid, {"limit": 2}), ids) == ["stripe", "acme"]


def test_a_query_that_names_nothing_falls_back_to_recency(four_jobs):
    uid, ids = four_jobs
    out = invoke("list_jobs", uid, {"query": "the Anthropic role"})
    assert out["matched"] == 0
    assert _order(out, ids) == ["stripe", "acme", "zendar", "never"]
    assert all(j["reason"].startswith("no match; ") for j in out["jobs"])


def test_the_lookup_is_scoped_to_the_user(four_jobs, isolated_engine):
    uid, ids = four_jobs
    other = _user(isolated_engine, "Other")
    _job(isolated_engine, other, "Zendar Lead", "Zendar", touched=utc_now())
    out = invoke("list_jobs", uid, {"query": "zendar"})
    assert set(j["job_id"] for j in out["jobs"]) == set(ids.values())


# ── every job-scoped reply names its job ─────────────────────────────────────

def test_job_scoped_replies_echo_the_job_they_acted_on(isolated_engine, env):
    uid, job_id, task = env
    label = {"job_id": job_id, "title": task["title"], "company": task["company"]}
    first = invoke("execute_plan", uid, {"program": {"job_id": job_id, "parent": None}})
    assert first["job"] == label
    for name, args in [("get_head", {}), ("history", {}), ("suggest_actions", {}),
                       ("art_briefing", {}), ("art_pins", {}),
                       ("checkout", {"node_id": first["node_id"]}),
                       ("render", {"format": "tex"})]:
        assert invoke(name, uid, {"job_id": job_id, **args})["job"] == label, name
    assert invoke("art_briefing", uid, {})["job"] is None         # no job given, none named
    missing = invoke("get_head", uid, {"job_id": "00000000-0000-0000-0000-000000000000"})
    assert missing["job"] is None
    assert invoke("checkout", uid, {"job_id": job_id, "node_id": "00000000-0000-0000-0000-"
                                    "000000000000"})["job"] is None   # an error names nothing


# ── the prompt hook ──────────────────────────────────────────────────────────

def _opened(engine, job_id, seconds):
    """The user opened the job in artie ui `seconds` from now (an explicit, ordered clock)."""
    with Session(engine) as s:
        job = s.get(JobDescription, UUID(job_id))
        job.last_touched_at = job.last_opened_at = utc_now() + timedelta(seconds=seconds)
        s.add(job)
        s.commit()


def test_the_prompt_hook_reports_a_job_switch_in_the_editor(isolated_engine):
    from harness import jobs
    from harness.hooks import session_start, user_prompt

    uid = _user(isolated_engine)
    a = _job(isolated_engine, uid, "Data Scientist", "Zendar")
    b = _job(isolated_engine, uid, "ML Engineer", "Stripe")
    _opened(isolated_engine, a, -60)                    # before this session
    payload = {"session_id": "s1", "prompt": "hi"}
    assert user_prompt(uid, payload) is None            # first prompt: set the cursors only

    _opened(isolated_engine, b, 1)
    text = user_prompt(uid, payload)
    assert text.count("\n") == 0
    assert "opened ML Engineer @ Stripe" in text and f"(job {b})" in text and "artie ui" in text
    assert user_prompt(uid, payload) is None            # reported once

    jobs.touch(uid, a)                                  # a host write is not a switch
    assert user_prompt(uid, payload) is None
    _opened(isolated_engine, b, 2)                      # reopening the job it is on
    assert user_prompt(uid, payload) is None

    _opened(isolated_engine, a, 3)                      # switching back is a switch
    text = user_prompt(uid, payload)
    assert "Data Scientist @ Zendar" in text and f"(job {a})" in text
    # The reported job is the one a compaction names.
    assert f"(job {a})" in session_start(uid, {"session_id": "s1", "source": "compact"})

    assert user_prompt(uid, {"session_id": "s2"}) is None   # a new session starts at "now"


def test_a_session_from_before_the_signal_starts_its_cursor_quietly(isolated_engine):
    from harness.hooks import save_state, user_prompt

    uid = _user(isolated_engine)
    a = _job(isolated_engine, uid, "Data Scientist", "Zendar")
    save_state("old", {"cursor": 0, "job_id": None})    # what a pre-#288 hook wrote
    _opened(isolated_engine, a, -5)
    assert user_prompt(uid, {"session_id": "old"}) is None
    _opened(isolated_engine, a, 1)
    assert f"(job {a})" in user_prompt(uid, {"session_id": "old"})


# ── old databases ────────────────────────────────────────────────────────────

def test_a_database_without_the_columns_migrates_and_its_jobs_read_as_never_touched(
        isolated_engine):
    import sqlalchemy as sa
    import database.db as db_module
    from harness import jobs

    uid = _user(isolated_engine)
    jid = _job(isolated_engine, uid, "Data Scientist", "Zendar")
    with isolated_engine.begin() as conn:               # the table as it was before #288
        conn.execute(sa.text("ALTER TABLE jobdescription DROP COLUMN last_touched_at"))
        conn.execute(sa.text("ALTER TABLE jobdescription DROP COLUMN last_opened_at"))
    cols = {c["name"] for c in sa.inspect(isolated_engine).get_columns("jobdescription")}
    assert "last_touched_at" not in cols

    db_module._migrate_db()
    db_module._migrate_db()                             # idempotent

    out = invoke("list_jobs", uid, {"query": "zendar"})
    assert [j["job_id"] for j in out["jobs"]] == [jid]
    assert out["jobs"][0]["last_touched_at"] is None
    assert out["jobs"][0]["reason"] == 'matched "zendar" (company); never touched'
    assert jobs.touch(uid, jid, opened=True) and jobs.latest_open(uid)[0] == jid
