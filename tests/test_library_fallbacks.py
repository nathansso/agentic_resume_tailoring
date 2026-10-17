"""The Jev-free variant and track picks (issue #352).

With no `TYPESAFE_API_KEY`, `ART_JEV_MODE=off` or an API error, `suggest_actions` and `open_job` run the
fallbacks of `harness/library.py`, which read the job's requirements through `harness/concepts.py`:

- a variant is picked when what it shows is what the job's top requirements ask for, and a bullet that
  repeats the posting's nouns while describing teaching or office work is not (#229 picked it);
- a track is picked from the job's duties and not its title, and only when its role family clearly leads.

Every test runs the real path (`suggest_actions`, `open_job`) with Jev off and a client that fails the
test if anything reaches for one. The labelled figures are pinned in `tests/test_fallback_benchmark.py`
(the fallback rows) and here (the fitted numbers, leave-one-out and the holdout).
"""

from uuid import UUID

import pytest
from sqlmodel import Session

import database.db as db
from eval import fit_library_fallbacks as fit_fb
from harness import concepts, library
from harness.contract import invoke
from harness.decisions import engine
from test_bullet_library import (  # noqa: F401  (helpers; `env` is a fixture)
    PIPELINE_REQUIREMENTS, _committed, _crafted_baseline, _curate, _import, _open_with_requirements,
)
from test_executor import EXP, env  # noqa: F401

PAYMENTS = [
    {"text": "Design and build gRPC and REST services that move money reliably", "type": "required", "criticality": 5},
    {"text": "Handle idempotency, retries and failure modes in distributed systems", "type": "required", "criticality": 4},
    {"text": "Strong Go, Java or Kotlin and relational database skills, preferably PostgreSQL", "type": "required", "criticality": 4},
    {"text": "Write thorough automated tests and review code", "type": "required", "criticality": 3},
    {"text": "Carry an on-call rotation for the services you own", "type": "required", "criticality": 3},
]
ETL_DUTIES = [
    {"text": "Build and maintain ETL pipelines into the data warehouse", "type": "required", "criticality": 5},
    {"text": "Own orchestration and scheduling of daily batch jobs", "type": "required", "criticality": 4},
    {"text": "Keep warehouse tables accurate, documented and tested", "type": "required", "criticality": 4},
    {"text": "Write SQL and Python to move data between systems", "type": "required", "criticality": 3},
]
STATS_DUTIES = [
    {"text": "Fit statistical and causal models to estimate the effect of product changes", "type": "required", "criticality": 5},
    {"text": "Design experiments and analyse their results", "type": "required", "criticality": 4},
    {"text": "Forecast demand and report uncertainty", "type": "required", "criticality": 4},
    {"text": "Present findings to leadership", "type": "required", "criticality": 3},
]
SALES_DUTIES = [
    {"text": "Run technical demos of the data platform for prospective customers", "type": "required", "criticality": 5},
    {"text": "Scope and support customer proofs of concept", "type": "required", "criticality": 4},
    {"text": "Answer technical questions on sales calls", "type": "required", "criticality": 4},
    {"text": "Work with account executives to close deals", "type": "required", "criticality": 3},
]


@pytest.fixture(autouse=True)
def jev_off(monkeypatch):
    """Everything here is the Jev-free path, and nothing may reach for a client."""
    monkeypatch.setenv("ART_JEV_MODE", "off")
    monkeypatch.setattr(engine, "get_client", lambda: pytest.fail("a fallback reached for a Jev client"))


def _item(uid, job_id):
    return {i["item_key"]: i for i in invoke("suggest_actions", uid, {"job_id": job_id})["items"]}[EXP]


# ── variant choice ───────────────────────────────────────────────────────────

def test_a_keyword_stuffed_variant_is_not_picked_where_word_overlap_picked_it(env):
    """The bullet repeats the posting's nouns (REST, API, microservices) but is about grading. #229's
    overlap scored it above its floor and picked it; the fallback reads what the bullet is about."""
    uid, _, _ = env
    job_id = _open_with_requirements(uid, "Backend Engineer, Payments", PAYMENTS)
    stuffed = "Graded 90 REST API and microservices assignments."
    _import(uid, EXP, stuffed)
    item = _item(uid, job_id)
    assert item["match"] == "no_match" and item["variant"] is None and item["source"] == "fallback"
    assert item["best_score"] == 0.0
    # what #229 computed: this bullet's overlap with the posting's words, above its 0.10 floor
    text = "Backend Engineer, Payments. " + " ".join(r["text"] for r in PAYMENTS)
    assert library.variant_score(stuffed, library.job_terms(None, text)) >= 0.10


def test_a_clear_match_is_picked_beside_the_stuffed_variant_and_the_unrelated_one(env):
    uid, _, _ = env
    job_id = _open_with_requirements(uid, "Backend Engineer, Payments", PAYMENTS)
    honest = "Designed gRPC and REST endpoints for a payments ledger in Go, handling idempotent retries for 2M requests a day."
    for text in ("Graded 90 REST API and microservices assignments.",
                 "Facilitated daily standups and sprint retrospectives for a team of 6.", honest):
        _import(uid, EXP, text)
    item = _item(uid, job_id)
    assert item["match"] == "variant" and item["variant"]["text"] == honest and item["source"] == "fallback"
    assert item["variant"]["score"] == item["best_score"] > library.VARIANT_MATCH_FLOOR
    assert item["approved_variants"] == 3


def test_a_variant_for_another_kind_of_work_is_no_match(env):
    uid, _, _ = env
    job_id = _open_with_requirements(uid, "Backend Engineer, Payments", PAYMENTS)
    _import(uid, EXP, "Trained a ResNet-50 on 1.2M images with PyTorch across 4 GPUs, improving top-1 accuracy.")
    assert _item(uid, job_id)["match"] == "no_match"


def test_a_job_with_nothing_to_read_gets_no_match(env):
    """No requirements and a title the vocabulary cannot place: nothing to judge a bullet against."""
    uid, _, _ = env
    job_id = _open_with_requirements(uid, "Associate", [])
    _import(uid, EXP, "Built Airflow ETL pipelines that load a Snowflake warehouse each night.")
    item = _item(uid, job_id)
    assert item["match"] == "no_match" and item["best_score"] == 0.0


def test_the_pick_does_not_depend_on_the_order_the_variants_are_stored_in(env):
    uid, _, _ = env
    job_id = _open_with_requirements(uid, "Data Engineer", PIPELINE_REQUIREMENTS)
    texts = ["Built Airflow ETL pipelines that load a Snowflake warehouse each night with retries and alerts.",
             "Scheduled nightly warehouse loads and ETL pipelines with Airflow and SQL, alerting on failures."]
    for t in texts:
        _import(uid, EXP, t)
    first = _item(uid, job_id)["variant"]["variant_id"]
    reordered = library.approved_by_item(uid)[EXP][::-1]
    terms = library.job_terms(None, "Data Engineer")
    reading = library.job_reading("Data Engineer", PIPELINE_REQUIREMENTS)
    chosen, _ = library.best_variant(reordered, terms, reading)
    assert chosen["variant_id"] == first


# ── track baseline ───────────────────────────────────────────────────────────

def _tracks(uid, job_id):
    """The env job's version saved as data_engineering and, curated, as data_science."""
    de, _ = _crafted_baseline(uid, job_id, "data_engineering", lambda c: None)
    ds, _ = _crafted_baseline(uid, job_id, "data_science", _curate)
    return de, ds


def _open(uid, title, requirements, **metadata):
    out = invoke("open_job", uid, {"jd_text": f"{title}. " + " ".join(r["text"] for r in requirements),
                                   "requirements": requirements,
                                   "metadata": {"title": title, "company": "Second Co", **metadata}})
    assert out.get("error") is None, out
    return out


def test_a_job_whose_title_and_duties_disagree_follows_the_duties(env):
    uid, job_id, _ = env
    de, ds = _tracks(uid, job_id)
    out = _open(uid, "Data Scientist", ETL_DUTIES)              # the title says data science, the duties say pipelines
    assert out["role_family"] == "data_science" and out["role_family_source"] == "title"
    assert out["baseline"]["track"] == "data_engineering" and out["baseline"]["node_id"] == de
    assert out["baseline"]["source"] == "fallback" and out["baseline"]["p"] is None


def test_a_clear_match_is_picked(env):
    uid, job_id, _ = env
    _, ds = _tracks(uid, job_id)
    out = _open(uid, "Analytics Engineer", STATS_DUTIES, company="Third Co")     # the title says pipelines, the duties statistics
    assert out["baseline"]["track"] == "data_science" and out["baseline"]["node_id"] == ds
    assert out["baseline"]["source"] == "fallback"
    plain = _open(uid, "Data Engineer", ETL_DUTIES, company="Fourth Co")
    assert plain["baseline"]["track"] == "data_engineering"


def test_duties_that_fit_no_saved_track_start_from_none_though_the_title_names_one(env):
    uid, job_id, _ = env
    _tracks(uid, job_id)
    out = _open(uid, "Data Engineer, Sales", SALES_DUTIES)       # #229's title lookup said data_engineering
    assert out["role_family"] == "data_engineering"
    assert out["baseline"] is None


def test_the_hosts_role_family_naming_a_saved_track_still_wins(env):
    uid, job_id, _ = env
    _, ds = _tracks(uid, job_id)
    out = _open(uid, "Data Scientist", ETL_DUTIES, role_family="data_science")
    assert out["role_family_source"] == "host"
    assert out["baseline"]["track"] == "data_science" and out["baseline"]["node_id"] == ds
    assert out["baseline"]["source"] == "host"


def test_a_job_with_no_requirements_keeps_the_title_lookup(env):
    uid, job_id, _ = env
    _, ds = _tracks(uid, job_id)
    out = _open(uid, "Data Scientist", [])
    assert out["baseline"]["track"] == "data_science" and out["baseline"]["source"] == "fallback"


def test_a_track_not_named_for_a_family_reads_as_the_family_of_the_job_it_came_from():
    tracks = [{"track": "analytics", "title": "Data Analyst"}, {"track": "wizardry", "title": "Wizard"}]
    assert library.track_family("analytics", "Data Analyst") == "data_science"
    assert library.track_family("wizardry", "Wizard") is None
    assert library.track_family("machine_learning", "") == "machine_learning"
    assert library.pick_track("Analytics Engineer", STATS_DUTIES, tracks) == "analytics"
    assert library.pick_track("Analytics Engineer", STATS_DUTIES, tracks[1:]) is None      # nothing it can place


def test_two_tracks_for_one_family_pick_the_one_named_for_it():
    tracks = [{"track": "ds_a", "title": "Data Scientist"}, {"track": "data_science", "title": "Data Scientist"}]
    assert library.pick_track("Data Scientist", STATS_DUTIES, tracks) == "data_science"


def test_a_pick_needs_both_the_floor_and_the_lead(monkeypatch):
    tracks = [{"track": "machine_learning", "title": "ML Engineer"}, {"track": "data_science", "title": "Data Scientist"}]
    found = library.track_scores("Data Scientist", STATS_DUTIES, tracks)
    assert found["track"] == "data_science" and found["score"] >= library.TRACK_FALLBACK_FLOOR
    assert abs(found["margin"] - (found["score"] - found["rival"])) < 1e-3
    assert found["margin"] >= library.TRACK_FALLBACK_MARGIN
    assert library.pick_track("Data Scientist", STATS_DUTIES, tracks) == "data_science"
    monkeypatch.setattr(library, "TRACK_FALLBACK_MARGIN", found["margin"] + 0.01)          # the lead falls short
    assert library.pick_track("Data Scientist", STATS_DUTIES, tracks) is None
    monkeypatch.setattr(library, "TRACK_FALLBACK_MARGIN", 0.0)
    monkeypatch.setattr(library, "TRACK_FALLBACK_FLOOR", found["score"] + 0.01)            # the score falls short
    assert library.pick_track("Data Scientist", STATS_DUTIES, tracks) is None


# ── the vocabulary ───────────────────────────────────────────────────────────

def test_spellings_of_one_skill_read_alike():
    assert concepts.read("trained a scikit-learn classifier") == concepts.read("trained a sklearn classifier")
    assert concepts.read("PyTorch training loop on GPUs") == concepts.read("torch training loop on gpus")
    assert concepts.read("services in postgres") == concepts.read("services in PostgreSQL")


def test_go_is_the_language_only_in_its_own_capitalization():
    assert "backend" in concepts.read("Built a Go service")
    assert "backend" not in concepts.read("We go the extra mile")


def test_an_ab_test_is_statistics_not_software_testing():
    assert "stats" in concepts.read("Ran an A/B testing readout") and "testing" not in concepts.read("Ran an A/B testing readout")
    assert "testing" in concepts.read("Raised unit test coverage")


def test_a_title_alone_is_thin_evidence_and_requirements_make_it_firm():
    thin = concepts.read_job("Machine Learning Engineer", [])
    firm = concepts.read_job("Machine Learning Engineer", [
        {"text": "Train deep learning models in PyTorch", "type": "required", "criticality": 5},
        {"text": "Deploy models and monitor drift", "type": "required", "criticality": 4},
        {"text": "Strong Python", "type": "required", "criticality": 3}])
    assert max(thin.values()) < 0.15 < max(firm.values())
    assert concepts.read_job("", []) == {}


def test_incidental_requirements_take_no_part_and_the_most_critical_come_first():
    rows = [{"text": f"requirement {i}", "type": "required", "criticality": 1 + i % 5} for i in range(20)]
    rows.append({"text": "Nice to have: Kubernetes", "type": "incidental", "criticality": 5})
    picked = concepts.requirement_rows(rows)
    assert len(picked) == concepts.MAX_REQUIREMENTS and all(c == 5 for c, _ in picked[:4])
    assert all("Kubernetes" not in t for _, t in picked)


def test_a_text_that_is_mostly_unasked_work_scores_zero():
    job = concepts.read_job("Backend Engineer", PAYMENTS)
    assert library.variant_fit("Graded 90 REST API and microservices assignments.", job) == 0.0
    assert library.variant_fit("Designed gRPC and REST endpoints in Go handling idempotent retries.", job) > 0.3
    # asked for, it counts: a teaching posting does want a teaching bullet
    teaching = concepts.read_job("Teaching Assistant", [
        {"text": "Grade assignments and give feedback to students", "type": "required", "criticality": 5},
        {"text": "Lead weekly lab sections", "type": "required", "criticality": 4}])
    assert library.variant_fit("Graded 90 assignments and led lab sections for 30 students.", teaching) > 0.3


# ── the fit, pinned ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def measured_fit():
    return fit_fb.measure()


def test_the_fitted_numbers_are_the_ones_in_the_policy(measured_fit):
    assert measured_fit["fit"] == {"variant_floor": library.VARIANT_MATCH_FLOOR,
                                   "track_floor": library.TRACK_FALLBACK_FLOOR,
                                   "track_margin": library.TRACK_FALLBACK_MARGIN} == {
        "variant_floor": 0.09, "track_floor": 0.30, "track_margin": 0.02}


def test_the_fitted_figures_meet_the_bars_harmful_errors_first(measured_fit):
    """#350's bars: 0 poor-fit picks and at most 1 other wrong, at least 33/48 and above 'always no_match' (15);
    0 wrong tracks, at least 31/36 and above 'always none' (14)."""
    v, t = measured_fit["variant"], measured_fit["track"]
    assert v["fitted"]["poor"] == 0 and v["fitted"]["wrong"] <= 1
    assert v["fitted"]["correct"] >= 33 and v["fitted"]["correct"] > v["constant"]["correct"] == 15
    assert t["fitted"]["wrong"] == 0
    assert t["fitted"]["correct"] >= 31 and t["fitted"]["correct"] > t["constant"]["correct"] == 14


def test_leave_one_out_and_the_holdout_are_pinned_and_harmless(measured_fit):
    """Neither figure may show a harmful error. The holdout was written by the author before the vocabulary
    and is not independent of it (see eval/fit_library_fallbacks.py)."""
    v, t, loo = measured_fit["variant"], measured_fit["track"], measured_fit["loo"]
    assert (loo["variant"]["correct"], loo["variant"]["poor"], loo["variant"]["wrong"]) == (41, 0, 1)
    assert (loo["track"]["correct"], loo["track"]["wrong"]) == (36, 0)
    assert (v["holdout"]["n"], v["holdout"]["correct"], v["holdout"]["poor"], v["holdout"]["wrong"]) == (22, 19, 0, 1)
    assert (t["holdout"]["n"], t["holdout"]["correct"], t["holdout"]["wrong"]) == (17, 15, 0)
    assert v["holdout"]["correct"] > v["holdout_constant"]["correct"] == 8
    assert t["holdout"]["correct"] > t["holdout_constant"]["correct"] == 6


def test_every_wrong_track_on_the_labels_trails_its_rival(measured_fit):
    """Why the margin is not bound by a wrong pick: each job no track fits has another family ahead of the
    best saved track, so the lead is negative."""
    rows = measured_fit["rows"]["track"]
    for r in rows:
        if r["category"] in ("no_track", "near_miss"):
            assert r["margin"] < 0, r["id"]
        else:
            assert r["margin"] >= library.TRACK_FALLBACK_MARGIN and r["pick0"] in r["best"], r["id"]


def test_the_committed_fit_report_is_current(measured_fit):
    assert fit_fb.REPORT_PATH.read_text(encoding="utf-8").replace("\r\n", "\n") == fit_fb.render(measured_fit)


def test_the_fit_script_runs_with_no_network_or_key(tmp_path):
    import subprocess
    import sys

    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    proc = subprocess.run([sys.executable, str(root / "eval" / "fit_library_fallbacks.py"), "check"],
                          capture_output=True, text=True, cwd=root, timeout=240)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_holdout_files_are_the_authors_and_say_so():
    import json

    for name in ("variants.json", "baselines.json"):
        doc = json.loads((fit_fb.HOLDOUT_DIR / name).read_text(encoding="utf-8"))
        assert "holdout" in doc["status"] and "unreviewed" in doc["status"], name
        assert "recordings" not in doc
