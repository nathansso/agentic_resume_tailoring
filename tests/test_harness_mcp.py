"""The read-only `art-mcp` spike (issue #189): tools, keys, read-only, boundary."""

import asyncio
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlmodel import Session

from conftest import _seed_user_and_skill
from database.models import (
    Achievement, Education, Experience, Project, ProjectBlurb, UserPreference,
)

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def kg(isolated_engine, monkeypatch):
    """A seeded user: skill, experience, project (+blurb), education, achievement,
    three preferences in different scopes, and one completed job with a card."""
    import agents.job_card as jc
    import services

    user = _seed_user_and_skill(isolated_engine)
    uid = user.user_id
    with Session(isolated_engine) as s:
        s.add(Experience(user_id=uid, title="Data Science Intern", company="IDX Exchange",
                         description="Forecasting home prices",
                         bullets=["Built a gradient-boosted ensemble on 200K listings"]))
        proj = Project(user_id=uid, name="Next-Item Recommendation",
                       description="Session-based recommendation with a GNN baseline")
        s.add(proj)
        s.add(Education(user_id=uid, institution="UC San Diego", degree="M.S. Data Science"))
        s.add(Achievement(user_id=uid, title="1st Place Overall", issuer="Memory Meets Motion"))
        s.commit()
        s.add(ProjectBlurb(project_id=proj.project_id, style="metrics",
                           content="Compared XGBoost against a graph neural network"))
        s.add(UserPreference(user_id=uid, text="Never mention coursework projects",
                             polarity="suppress", target_key="proj:coursework-db",
                             scope_type="global", strength=5))
        s.add(UserPreference(user_id=uid, text="Lead with forecasting work",
                             polarity="emphasize", scope_type="role_family",
                             scope_value="data_science", strength=3))
        s.add(UserPreference(user_id=uid, text="Lead with systems work",
                             polarity="emphasize", scope_type="role_family",
                             scope_value="swe", strength=4))
        s.commit()

    from test_job_card import _seed_job_and_result
    monkeypatch.setattr(jc, "classify_role_family", lambda *a, **k: "data_science")
    job_id, _ = _seed_job_and_result(isolated_engine, uid, title="Data Scientist",
                                     company="Rippling")
    assert services.rebuild_job_card(uid, job_id) is not None
    return uid


@contextmanager
def _reads_only(engine):
    """Fail the test on any statement that is not a read."""
    seen = []

    def _check(conn, cursor, statement, params, context, executemany):
        head = statement.lstrip().split(None, 1)[0].upper()
        if head not in {"SELECT", "PRAGMA", "WITH", "SHOW", "SET"}:
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", _check)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", _check)
    assert not seen, f"harness tools wrote to the DB: {seen[:3]}"


# ── art_briefing ─────────────────────────────────────────────────────────────

def test_briefing_pins_strength_five_verbatim_and_scopes_the_rest(kg, isolated_engine):
    from harness import tools

    with _reads_only(isolated_engine):
        b = tools.art_briefing(kg, role_family="data_science")
    assert [p["text"] for p in b["pins"]] == ["Never mention coursework projects"]
    texts = [p["text"] for p in b["preferences"]]
    assert "Lead with forecasting work" in texts
    assert "Lead with systems work" not in texts            # another role family
    assert texts[0] == "Never mention coursework projects"  # strength-sorted
    assert "Data Scientist" in b["job_cards"]
    assert b["counts"]["job_cards"] == 1


# ── kg_search ────────────────────────────────────────────────────────────────

def test_kg_search_ranks_the_matching_item_first_and_is_deterministic(kg, isolated_engine):
    from harness import tools

    with _reads_only(isolated_engine):
        first = tools.kg_search(kg, "gradient boosted forecasting")
        again = tools.kg_search(kg, "gradient boosted forecasting")
    assert first == again
    assert first[0]["key"] == "exp:data science intern|idx exchange"


def test_kg_search_searches_project_blurbs_and_filters_by_kind(kg):
    from harness import tools

    hits = tools.kg_search(kg, "xgboost graph neural network")
    assert hits[0]["key"] == "proj:next-item recommendation"
    only_skills = tools.kg_search(kg, "python recommendation", kinds=["skill"])
    assert [h["kind"] for h in only_skills] == ["skill"]
    assert tools.kg_search(kg, "the and of") == []


# ── get_item ─────────────────────────────────────────────────────────────────

def test_every_search_key_round_trips_through_get_item(kg, isolated_engine):
    from harness import tools

    keys = [h["key"] for h in tools.kg_search(
        kg, "python idx recommendation san diego place", limit=20)]
    assert {k.split(":", 1)[0] for k in keys} == {"skill", "exp", "proj", "edu", "ach"}
    with _reads_only(isolated_engine):
        for key in keys:
            item = tools.get_item(kg, key)
            assert item.get("key") == key, item
    proj = tools.get_item(kg, "proj:next-item recommendation")["record"]
    assert proj["blurbs"][0]["content"].startswith("Compared XGBoost")


def test_get_item_unknown_key_returns_suggestions_not_an_exception(kg):
    from harness import tools

    out = tools.get_item(kg, "proj:next item recommender")
    assert out["error"] == "not_found"
    assert "proj:next-item recommendation" in out["suggestions"]
    assert tools.get_item(kg, "bogus")["error"] == "not_found"


def test_keys_match_the_planner_convention():
    """The executor (#197) will receive these keys; they must be the planner's."""
    from agents.tailor import ResumeTailorAgent as TailorAgent
    from harness import tools

    exp = {"title": " Data Science Intern ", "company": "IDX Exchange"}
    proj = {"name": "Next-Item Recommendation"}
    assert tools.exp_key(exp) == TailorAgent._exp_key(exp)
    assert tools.proj_key(proj) == TailorAgent._proj_key(proj)


# ── server ───────────────────────────────────────────────────────────────────

def test_database_url_never_leaks_in_from_the_environment():
    from harness.mcp_server import resolve_database_url

    env = {"DATABASE_URL": "postgresql://prod/secret", "ART_DATA_DIR": "/tmp/artdata"}
    assert resolve_database_url(None, env) == f"sqlite:///{Path('/tmp/artdata') / 'art.db'}"
    url = resolve_database_url("dotenv", {}, dotenv_reader=lambda: "postgresql://u@h/db")
    assert url.startswith("postgresql://u@h/db?options=")
    assert "default_transaction_read_only%3Don" in url
    via_env = resolve_database_url(None, {"ART_MCP_DATABASE_URL": "sqlite:///x.db"})
    assert via_env == "sqlite:///x.db"
    with pytest.raises(SystemExit):
        resolve_database_url("dotenv", {}, dotenv_reader=lambda: None)


def test_server_registers_exactly_the_three_read_only_tools():
    pytest.importorskip("mcp")
    from harness.mcp_server import TOOL_NAMES, build_server

    listed = asyncio.run(build_server(None).list_tools())
    assert sorted(t.name for t in listed) == sorted(TOOL_NAMES)
    assert all(t.annotations and t.annotations.read_only_hint for t in listed)


def test_harness_imports_no_generative_client():
    """Early form of #190's boundary test, in a clean interpreter."""
    code = (
        "import sys; import harness.tools, harness.mcp_server; "
        "bad = sorted({m.split('.')[0] for m in sys.modules} & "
        "{'llm','langchain','langchain_core','langchain_openai','langchain_anthropic',"
        "'openai','anthropic','langgraph'}); print(','.join(bad))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                         text=True, env={**__import__("os").environ,
                                         "DATABASE_URL": "sqlite:///:memory:"})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", f"harness pulled in: {out.stdout.strip()}"
