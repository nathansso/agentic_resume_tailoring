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


def test_kg_search_uses_fts_stemming(kg):
    """#194: the #189 run's lexical match missed inflections."""
    from harness import tools

    hits = tools.kg_search(kg, "forecasts listing")
    assert hits[0]["key"] == "exp:data science intern|idx exchange"


def test_kg_search_does_not_let_bare_skill_names_outrank_the_experience(kg):
    """#189: a skill's whole text is its name, so at title weight every
    one-word skill outranked the experience that used it."""
    from harness import tools
    from harness.ingest import upsert_items

    upsert_items(kg, [{"kind": "skill", "data": {"name": "Forecasting"}}])
    keys = [h["key"] for h in tools.kg_search(kg, "forecasting home prices")]
    assert keys.index("exp:data science intern|idx exchange") < keys.index("skill:forecasting")


def test_kg_search_falls_back_to_the_lexical_scorer_without_fts5(kg, monkeypatch):
    from harness import tools

    monkeypatch.setattr(tools, "fts_search", lambda *a, **k: None)
    hits = tools.kg_search(kg, "gradient boosted forecasting")
    assert hits[0]["key"] == "exp:data science intern|idx exchange"
    assert hits[0]["score"] == 3.0        # the old scorer: three body-token matches


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


# ── projects without blurbs (#260) ───────────────────────────────────────────

SEMANTIC = ("Semantic search library. Open-source semantic search library using "
            "sentence-transformers and FAISS for retrieval.")


@pytest.mark.parametrize("name, description, kept", [
    # The #260 case: the lead-in's words all come right after it.
    ("semantic-search", SEMANTIC, "Open-source semantic search library using "
                                  "sentence-transformers and FAISS for retrieval."),
    # It restates the name (plus a type word) and nothing follows to repeat it.
    ("Pixel Adventure", "Pixel adventure game. Platformer with procedural levels.",
     "Platformer with procedural levels."),
])
def test_a_lead_in_that_only_restates_the_project_is_dropped(name, description, kept):
    from harness.tools import drop_redundant_lead_in

    assert drop_redundant_lead_in(description, name) == kept


@pytest.mark.parametrize("name, description", [
    ("recsys", "Built a recommender. It serves ten thousand users daily."),     # words not repeated
    ("recsys", "Cut latency 40%. Cut latency 40% with a Redis cache."),         # a number is a fact
    ("search", "Kubernetes operator. Operator that scales batch jobs."),        # technology not repeated
    ("search", "Won first place. Judges cited the clean API and the demo."),    # a result not repeated
    ("search", "A deliberately long opening sentence that restates nothing at all today. "
               "A deliberately long opening sentence that restates nothing at all today again."),
    ("semantic-search", "Semantic search library"),                             # one sentence: keep it all
    ("semantic-search", "Semantic search library."),                            # never the whole description
    ("search", "Uses e.g. Redis. Uses e.g. Redis for caching."),                # an abbreviation, not a stop
    ("search", "Python CLI. Command-line search over notes."),                  # Python never repeats
    ("search", "Node.js app.\nServes the search API."),                          # restates nothing
    ("", ""),
])
def test_a_lead_in_that_carries_anything_is_kept(name, description):
    from harness.tools import drop_redundant_lead_in

    assert drop_redundant_lead_in(description, name) == description.strip()


def test_a_project_with_blurbs_renders_its_blurbs_unchanged():
    from harness.tools import project_source_bullets

    record = {"name": "semantic-search", "description": SEMANTIC,
              "blurbs": [{"style": "host", "content": "Built semantic search over 40k docs."},
                         {"style": "host", "content": ""}]}
    assert project_source_bullets(record) == ["Built semantic search over 40k docs."]
    assert project_source_bullets(dict(record, blurbs=[]))[0].startswith("Open-source semantic")


def _put_project(uid, description, blurbs=None, name="semantic-search"):
    from harness.ingest import upsert_items

    data = {"name": name, "description": description, **({"blurbs": blurbs} if blurbs else {})}
    out = upsert_items(uid, [{"kind": "project", "data": data}])
    assert out["counts"] == {"created": 1}, out
    return f"proj:{name}"


def test_get_item_flags_a_project_whose_description_is_not_a_bullet(kg):
    from harness import tools

    key = _put_project(kg, SEMANTIC)
    item = tools.get_item(kg, key)["record"]
    assert (item["incomplete"], item["missing"]) == (True, ["blurbs"])
    assert item["description"] == SEMANTIC                 # stored as written


def test_get_item_does_not_flag_a_bullet_like_description_or_a_project_with_blurbs(kg):
    from harness import tools

    short = _put_project(kg, "Built semantic search over 40k documents with FAISS.", name="a")
    blurbed = _put_project(kg, SEMANTIC, blurbs=["Built semantic search over 40k docs."], name="b")
    empty = _put_project(kg, "", name="c")
    assert tools.get_item(kg, short)["record"]["incomplete"] is False
    assert tools.get_item(kg, blurbed)["record"]["incomplete"] is False
    assert tools.get_item(kg, empty)["record"]["missing"] == ["blurbs"]
    seeded = tools.get_item(kg, "proj:next-item recommendation")["record"]
    assert (seeded["incomplete"], seeded["missing"]) == (False, [])
