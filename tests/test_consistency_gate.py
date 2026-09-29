"""The numeric and entity consistency gate (issue #123).

Every number, date, duration, money or scale figure and every proper noun or
technology name in a *cited* bullet must appear in its cited evidence. The
check is regex and word lists, so these tests need no model, no store for the
unit half, and pin what the gate flags, what it lets through, and how it is
scoped.
"""

import pytest

from agents.checks import consistency_check, extract_claims, unsupported_tokens
from harness.acceptance import Context, accept, consistency_violations, metric_vector
from test_citations import _cited, _revise, _src, base_content  # noqa: F401
from test_executor import EXP, EXP2, _run, env  # noqa: F401  (fixture)


def _keys(text, generous=False):
    return {k for _, k, _ in extract_claims(text, generous=generous)}


# ── extraction and normalization ─────────────────────────────────────────────

@pytest.mark.parametrize("a,b", [
    ("served 1,000,000 users", "served 1M users"),
    ("served 1,000,000 users", "served 1 million users"),
    ("served one million users", "served 1,000,000 users"),
    ("a team of twelve", "a team of 12"),
    ("twenty-five engineers", "25 engineers"),
    ("2 years", "24 months"),
    ("a 2-week sprint", "a two-week sprint"),
    ("14 days", "2 weeks"),
    ("120ms", "0.12 seconds"),
    ("40k requests/minute", "40,000 requests per minute"),
    ("10k events/second", "10,000 events per second"),
    ("$5M", "5 million dollars"),
    ("$1.2M", "1,200,000"),
    ("40%", "40 percent"),
    ("50GB", "50 gigabytes"),
    ("4x", "4 times"),
    ("Jun 2026", "June 2026"),
    ("3-tier", "three-tier"),
])
def test_reformatting_normalizes_to_the_same_claim(a, b):
    assert _keys(a) == _keys(b, generous=True), (a, b)
    # Read the other way round the bullet side is conservative: it may read
    # fewer claims (a hyphenated "three-tier"), never different ones.
    assert _keys(b) <= _keys(a, generous=True), (a, b)


@pytest.mark.parametrize("a,b", [
    ("40%", "45%"), ("40k requests/minute", "400k requests/minute"),
    ("2 years", "3 years"), ("40%", "40"), ("120ms", "120s"),
    ("40k requests/minute", "40k requests/hour"), ("June 2026", "July 2026"),
])
def test_a_changed_value_or_unit_is_a_different_claim(a, b):
    assert _keys(a) != _keys(b)


def test_number_words_are_read_conservatively_in_a_bullet():
    # Idioms are not claims on the bullet side ...
    assert _keys("one of the owners of a zero-downtime, two-way, three-tier system") == set()
    # ... but the evidence side reads every spelling, so "3-tier" is supported.
    assert _keys("three-tier", generous=True) == _keys("3-tier")
    assert unsupported_tokens("Owned a 3-tier service", ["Owned a three-tier service"]) == []


def test_extraction_reports_the_surface_the_user_wrote():
    got = extract_claims("Served twelve teams and 1,000,000 users at $5M ARR in Jun 2026.")
    assert [s for _, _, s in got] == ["twelve", "1,000,000", "$5M", "Jun 2026"]


# ── the per-bullet check ─────────────────────────────────────────────────────

def test_an_invented_percentage_is_caught_and_named():
    bullet = "Improved API performance by 40%, reducing p99 latency to 120ms"
    assert unsupported_tokens(bullet, ["Improved API performance"]) == ["40%", "p99", "120ms"]


def test_reformatted_figures_are_not_flagged():
    src = ["Served one million users at 40,000 requests per minute over two years."]
    assert unsupported_tokens("Served 1M users at 40k requests/min over 24 months.", src) == []


def test_a_changed_figure_is_flagged_not_just_a_missing_one():
    assert unsupported_tokens("Cut latency by 45%", ["Cut latency by 40%"]) == ["45%"]


def test_a_tighter_bullet_with_the_same_facts_passes():
    src = ["Built a Kafka pipeline in Python that processed 50GB of events daily for 12 teams."]
    assert unsupported_tokens("Built a Kafka pipeline in Python for 12 teams.", src) == []


@pytest.mark.parametrize("bullet,expected", [
    ("Built pipelines with Kafka and Spark", ["Kafka", "Spark"]),
    ("Deployed to AWS with Terraform", ["AWS", "Terraform"]),
    ("Trained models in PyTorch", ["PyTorch"]),
    ("Wrote a service in C# and Node.js", ["C#", "Node.js"]),
    ("Partnered with engineers at Google", ["Google"]),
    ("Used kafka for ingestion", ["kafka"]),                  # lowercase, but a known name
])
def test_a_name_absent_from_the_evidence_is_flagged(bullet, expected):
    assert unsupported_tokens(bullet, ["Built data services for internal teams."]) == expected


@pytest.mark.parametrize("bullet", [
    "Led migration to event-driven services",                 # capitalised verb at the start
    "Designed a pipeline. Owned the rollout end to end",      # sentence start
    "Designed the ETL and API layers for the CI/CD flow",     # generic acronyms
    "Wrote SQL against a relational store",                   # PostgreSQL supports SQL (below)
    "Increased Team Velocity And Delivery Speed Across Squads",  # Headline Case
    "Managed a Machine Learning Engineer intern",             # title words
])
def test_capitalised_verbs_generic_words_and_headline_case_do_not_trip_it(bullet):
    assert unsupported_tokens(bullet, ["Built services on PostgreSQL."]) == []


def test_names_match_case_insensitively_through_aliases_and_plurals():
    src = ["Deployed PostgreSQL and Kubernetes on Amazon Web Services with scikit-learn."]
    bullet = "Ran postgres and k8s on AWS with sklearn, exposed through APIs"
    assert unsupported_tokens(bullet, src) == []


def test_a_name_inside_a_compound_is_still_checked():
    assert unsupported_tokens("Built a Kafka-based queue", ["Built a queue"]) == ["Kafka"]
    assert unsupported_tokens("Built a Kafka-based queue", ["Built a Kafka queue"]) == []
    # `GPT-4` is one name: a GPT-3.5 source does not support it.
    assert unsupported_tokens("Called GPT-4", ["Called GPT-3.5"]) == ["GPT-4"]


def test_derivations_are_flagged_by_design():
    # Strict to start (#123): "200 to 800 users" does not license "4x growth".
    assert unsupported_tokens("Drove 4x user growth", ["Grew users from 200 to 800."]) == ["4x"]


# ── scope: cited evidence, not the whole profile ─────────────────────────────

SOURCES = {
    "exp:engineer|acme": ["Led a team of 5 building the ingest service.",
                          "Cut costs on the billing service."],
    "proj:tool": ["Shipped a CLI used by 300 analysts with Docker."],
}


def _content(bullets, cites, key="exp:engineer|acme"):
    item = {"title": "Engineer", "company": "Acme", "bullets": bullets, "cites": cites}
    return {"experiences": [item]}


def test_a_number_elsewhere_in_the_profile_is_still_flagged():
    bullet = "Cut billing costs for 300 analysts"
    content = _content([bullet], {bullet: ["exp:engineer|acme#b1"]})
    assert consistency_check(content, SOURCES) == [
        ("exp:engineer|acme", bullet, ["300"])]


def test_a_cite_to_another_item_brings_that_items_evidence():
    bullet = "Shipped a CLI for 300 analysts"
    content = _content([bullet], {bullet: ["proj:tool#b0"]})
    assert consistency_check(content, SOURCES) == []


def test_the_items_own_source_bullets_count_even_when_not_cited():
    bullet = "Led 5 engineers on billing"
    content = _content([bullet], {bullet: ["exp:engineer|acme#b1"]})
    assert consistency_check(content, SOURCES) == []


def test_the_item_header_supports_its_own_title_company_and_dates():
    bullet = "Owned the ingest service as Engineer at Acme"
    content = _content([bullet], {bullet: ["exp:engineer|acme#b0"]})
    assert consistency_check(content, SOURCES) == []


def test_verbatim_and_uncited_bullets_are_skipped():
    content = _content(["Led a team of 5 building the ingest service.",
                        "Grew revenue 90% with Kafka."],
                       {"Led a team of 5 building the ingest service.": ["exp:engineer|acme#b0"]})
    assert consistency_check(content, SOURCES) == []           # uncited is #198's job
    (item,) = content["experiences"]
    item["cites"]["Grew revenue 90% with Kafka."] = ["exp:engineer|acme#b1"]
    assert consistency_check(content, SOURCES) == [
        ("exp:engineer|acme", "Grew revenue 90% with Kafka.", ["90%", "Kafka"])]


def test_an_unresolvable_cite_adds_no_evidence():
    bullet = "Grew revenue 90%"
    content = _content([bullet], {bullet: ["exp:nope|nowhere#b9"]})
    assert consistency_check(content, SOURCES) == [("exp:engineer|acme", bullet, ["90%"])]


# ── the gate ─────────────────────────────────────────────────────────────────

def _ctx(**kw):
    return Context(jd_text="", source_bullets=SOURCES, cite_status=lambda c: None, **kw)


def test_the_gate_names_each_unsupported_token_sorted_and_deterministically():
    a, b = "Cut billing costs by 30% using Kafka", "Cut billing costs for 300 analysts"
    cites = {a: ["exp:engineer|acme#b1"], b: ["exp:engineer|acme#b1"]}
    got = consistency_violations(_content([a, b], cites), _ctx())
    assert got == ["consistency:Cut billing costs by 30% using Kafka:30%",
                   "consistency:Cut billing costs by 30% using Kafka:Kafka",
                   "consistency:Cut billing costs for 300 analysts:300"]
    assert consistency_violations(_content([b, a], cites), _ctx()) == got
    assert consistency_violations(_content([a, b], cites), _ctx()) == got


def test_the_gate_is_off_without_a_cite_checker():
    bullet = "Grew revenue 90%"
    content = _content([bullet], {bullet: ["exp:engineer|acme#b1"]})
    assert consistency_violations(content, Context(jd_text="", source_bullets=SOURCES)) == []


def test_only_a_newly_introduced_violation_blocks():
    ctx = _ctx()
    old = "Grew revenue 90%"
    before = metric_vector(_content([old], {old: ["exp:engineer|acme#b1"]}), ctx)
    assert before["gates"]["consistency"] == ["consistency:Grew revenue 90%:90%"]
    same = accept(before, before, requested=True)
    assert same["accepted"] and "consistency" not in same["gate_violations"]

    new = "Cut costs 12%"
    after = metric_vector(_content([old, new], {old: ["exp:engineer|acme#b1"],
                                                new: ["exp:engineer|acme#b1"]}), ctx)
    verdict = accept(before, after, requested=True)
    assert not verdict["accepted"]
    assert verdict["gate_violations"] == {"consistency": ["consistency:Cut costs 12%:12%"]}
    assert verdict["reason"].startswith("hard_gate: consistency")


def test_a_vector_saved_before_the_gate_existed_still_compares():
    ctx = _ctx()
    bullet = "Cut costs 12%"
    after = metric_vector(_content([bullet], {bullet: ["exp:engineer|acme#b1"]}), ctx)
    legacy = metric_vector(_content(["Cut costs"], {}), ctx)
    del legacy["gates"]["consistency"]
    assert accept(legacy, after, requested=True)["gate_violations"] == {
        "consistency": ["consistency:Cut costs 12%:12%"]}


# ── through the executor ─────────────────────────────────────────────────────

def test_a_plan_node_that_invents_a_number_is_refused_by_the_executor(env):
    uid, job_id, _ = env
    src = _src(uid, EXP2)
    invented = [{"text": "Developed FastAPI and Flask microservices handling 40k requests/minute "
                         "behind an Nginx gateway, cutting p99 latency by 45%.",
                 "cites": [f"{EXP2}#b0"]}] + _cited(EXP2, src[1:3])
    for i in (1, 2):
        invented[i]["cites"] = [f"{EXP2}#b{i}"]
    out = _run(uid, {"job_id": job_id, "nodes": [_revise(EXP2, invented)]}, dry_run=True)
    node = out["nodes"][0]
    assert node["status"] == "reverted"
    assert node["reason"].startswith("hard_gate: consistency")
    assert "45%" in node["reason"] and "p99" in node["reason"]
    assert out["metrics"]["base"]["gates"]["consistency"] == []


def test_a_faithful_reformat_passes_the_gate_in_the_executor(env):
    uid, job_id, _ = env
    src = _src(uid, EXP2)
    reworded = [{"text": "Developed FastAPI and Flask microservices handling 40,000 requests per "
                         "minute behind an Nginx gateway.", "cites": [f"{EXP2}#b0"]}]
    reworded += [{"text": t, "cites": [f"{EXP2}#b{i}"]} for i, t in enumerate(src[1:3], 1)]
    out = _run(uid, {"job_id": job_id, "nodes": [_revise(EXP2, reworded)]}, dry_run=True)
    node = out["nodes"][0]
    assert "consistency" not in (node["reason"] or "")
    assert out["metrics"]["final"]["gates"]["consistency"] == []


def test_user_authored_uncited_text_never_blocks_a_plan(env):
    from harness import tree

    uid, job_id, _ = env
    content = base_content(uid, job_id)
    exp = next(e for e in content["experiences"] if e["title"] == "Machine Learning Engineer")
    exp["bullets"][0] = "My own wording: doubled recall to 99% with Cassandra."
    exp.pop("cites", None)
    node = tree.commit_node(uid, job_id, content=content, source="editor")
    out = _run(uid, {"job_id": job_id, "parent": node["node_id"],
                     "nodes": [{"id": "k", "op": "keep", "item_key": EXP}]})
    assert out["committed"], out["violations"]
    assert out["metrics"]["base"]["gates"]["consistency"] == []


# ── the metric ───────────────────────────────────────────────────────────────

def test_the_eval_metric_counts_what_the_gate_flags():
    from eval.metrics import compute_task_metrics, consistency_metrics

    a, b = "Cut billing costs by 30% using Kafka", "Cut billing costs by half"
    content = _content([a, b], {a: ["exp:engineer|acme#b1"], b: ["exp:engineer|acme#b1"]})
    got = consistency_metrics(content, SOURCES)
    assert (got["cited_bullets"], got["flagged_bullets"], got["unsupported_tokens"]) == (2, 1, 2)
    assert got["flagged"] == [{"item": "exp:engineer|acme", "bullet": a, "tokens": ["30%", "Kafka"]}]
    assert consistency_metrics({"experiences": []}, SOURCES)["unsupported_tokens"] == 0
    # Additive: the family appears only when source bullets are supplied.
    rolled = compute_task_metrics(content, "", {}, 0, {}, {}, source_bullets=SOURCES)
    assert rolled["consistency"]["flagged_bullets"] == 1
    assert "consistency" not in compute_task_metrics(content, "", {}, 0, {}, {})


# ── false positives on realistic rewrites ────────────────────────────────────
#
# Rewrites in the styles a host produces (tighten, keyword-weave, reformat,
# merge, generalize) over the synthetic benchmark profile (`eval/profiles/
# benchmark_profile.md`). No recorded model output exists in the repo (no
# committed cassettes, #182), so this set is hand-written and the measured rate
# is a floor for regressions, not an estimate of production precision.

FAITHFUL_REWRITES = {
    EXP: [
        "Deployed gradient-boosted and transformer ranking models on PyTorch and XGBoost, serving 2,000,000 daily predictions.",
        "Served 2M daily predictions from PyTorch and XGBoost ranking models.",
        "Built a Postgres and Redis feature store, cutting feature backfill from hours to minutes.",
        "Automated model retraining on Airflow with drift detection and alerting.",
        "Lifted recall@10 by 18 percent by fine-tuning sentence-transformer embedding models for semantic product search.",
        "Improved semantic product search recall@10 by 18% through fine-tuned embedding models.",
        "Owned ML infrastructure end to end: ranking models, feature store, monitoring and retraining.",
        "Operated production ML systems: model monitoring, drift detection, and automated retraining with Airflow.",
    ],
    EXP2: [
        "Built FastAPI and Flask microservices sustaining 40,000 requests per minute behind Nginx.",
        "Developed FastAPI and Flask microservices handling 40k requests/minute behind an Nginx gateway.",
        "Modeled billing and subscription data in PostgreSQL with SQLAlchemy and Alembic migrations.",
        "Containerized services with Docker and shipped them to Amazon Web Services ECS via GitHub Actions CI/CD.",
        "Led the monolith-to-event-driven migration on Kafka.",
        "Led migration from a monolith to event-driven microservices using Kafka.",
        "Shipped Dockerized services to AWS ECS through automated CI/CD pipelines.",
        "Designed backend REST APIs and data models for billing, on Postgres.",
    ],
    "exp:software engineer|harbor labs": [
        "Built React and TypeScript dashboards that visualize pipeline health for internal teams.",
        "Processed 50 GB of daily event data into Snowflake with Python ETL jobs and pandas.",
        "Wrote Python ETL jobs (pandas) loading 50GB/day of events into Snowflake.",
        "Halved the flaky-test rate by adding pytest integration tests.",
        "Built dashboards in React + TypeScript; added pytest integration tests that cut flaky tests by half.",
    ],
    "exp:student developer|city university it department": [
        "Automated report generation with Python, saving staff 10 hours a week.",
        "Maintained PHP and MySQL tooling for course registration.",
        "Saved staff ten hours weekly by automating reports with Python scripts.",
    ],
    "proj:semanticsearch-lite": [
        "Built an open-source semantic search library (sentence-transformers, FAISS, FastAPI) with 400+ GitHub stars.",
        "Implemented hybrid retrieval combining BM25 and dense search with reciprocal rank fusion.",
        "Created a semantic search library on FAISS and sentence-transformers, served over FastAPI; 400+ stars.",
    ],
    "proj:streamboard": [
        "Built a real-time analytics dashboard on Kafka and ClickHouse with a React frontend, processing 10,000 events per second.",
        "Processed 10k events/second in a real-time analytics dashboard (Kafka, ClickHouse, React).",
    ],
    "proj:llm resume coach": [
        "Built a resume critique app with LangChain and OpenAI that scores resumes against job descriptions, deployed on Railway with Docker.",
        "Deployed a LangChain + OpenAI resume critique application on Railway using Docker.",
    ],
    "proj:pixel adventure": [
        "Published a 2D platformer game built in C# and Unity on itch.io.",
        "Built and published a 2D platformer in Unity (C#).",
    ],
}


def _bench_sources(uid):
    from harness.executor import _KG
    return _KG(uid).source_bullets


def test_faithful_rewrites_are_not_flagged(env):
    """Measured on the synthetic benchmark profile: none of the faithful
    rewrites is flagged (the count is asserted so the set cannot silently
    shrink)."""
    uid, _, _ = env
    sources = _bench_sources(uid)
    total, flagged = 0, []
    for key, rewrites in FAITHFUL_REWRITES.items():
        for text in rewrites:
            total += 1
            bad = unsupported_tokens(text, sources[key])
            if bad:
                flagged.append((key, text, bad))
    assert total == 33
    assert flagged == []


def test_the_same_rewrites_with_one_fabrication_each_are_all_caught(env):
    uid, _, _ = env
    sources = _bench_sources(uid)
    missed = []
    for key, rewrites in FAITHFUL_REWRITES.items():
        for text in rewrites:
            for inject in (text.rstrip(".") + ", saving $3M.", text.rstrip(".") + " with Cassandra."):
                if not unsupported_tokens(inject, sources[key]):
                    missed.append(inject)
    assert missed == []
