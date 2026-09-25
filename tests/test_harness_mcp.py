"""The read-only `art-mcp` spike (issue #189): tools, keys, read-only, boundary."""

import asyncio
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlmodel import Session


ROOT = Path(__file__).resolve().parent.parent


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
    assert out["error"]["code"] == "not_found"
    assert "proj:next-item recommendation" in out["error"]["suggestions"]
    assert tools.get_item(kg, "bogus")["error"]["code"] == "not_found"


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


def test_server_registers_every_contract_tool():
    pytest.importorskip("mcp")
    from harness.contract import TOOLS
    from harness.mcp_server import TOOL_NAMES, build_server

    listed = asyncio.run(build_server(None).list_tools())
    assert sorted(t.name for t in listed) == sorted(TOOL_NAMES) == sorted(t.name for t in TOOLS)
    ro = {t.name: t.read_only for t in TOOLS}
    assert all(t.annotations.read_only_hint == ro[t.name] for t in listed)


def test_harness_imports_no_generative_client():
    """Early form of #190's boundary test, in a clean interpreter."""
    code = (
        "import sys; import harness.tools, harness.mcp_server, harness.cli, harness.contract, agents.checks; "
        "bad = sorted({m.split('.')[0] for m in sys.modules} & "
        "{'llm','langchain','langchain_core','langchain_openai','langchain_anthropic',"
        "'openai','anthropic','langgraph'}); print(','.join(bad))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                         text=True, env={**__import__("os").environ,
                                         "DATABASE_URL": "sqlite:///:memory:"})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", f"harness pulled in: {out.stdout.strip()}"
