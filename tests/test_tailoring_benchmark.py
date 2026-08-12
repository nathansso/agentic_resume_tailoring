"""
Tailoring efficacy benchmark tests (issue #51 Phase 1).

Covers the metric families in eval/metrics.py, the deterministic stub used for
offline runs, and an end-to-end smoke of eval/tailoring_benchmark.py driving
the real web API in a subprocess (its own isolated temp DB + env, so it can
never touch the developer's ~/.art data or this process's engine).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from eval.metrics import (
    OVER_REPEAT_THRESHOLD,
    ats_summary,
    experience_allocation,
    redundancy_metrics,
    skills_metrics,
    spearman,
)

ROOT = Path(__file__).resolve().parent.parent


# ── spearman ───────────────────────────────────────────────────────────────────

def test_spearman_perfect_and_inverse():
    assert spearman([1, 2, 3], [10, 20, 30]) == 1.0
    assert spearman([1, 2, 3], [30, 20, 10]) == -1.0


def test_spearman_undefined_cases():
    assert spearman([1], [2]) is None                # n < 2
    assert spearman([1, 1, 1], [1, 2, 3]) is None    # zero variance
    assert spearman([1, 2], [1, 2, 3]) is None       # length mismatch


def test_spearman_handles_ties():
    r = spearman([1, 2, 2, 3], [1, 2, 3, 4])
    assert r is not None and 0 < r <= 1


# ── experience allocation ──────────────────────────────────────────────────────

def _content(experiences=None, projects=None, skills_ranked=None, emphasized=None):
    return {
        "experiences": experiences or [],
        "projects": projects or [],
        "skills_ranked": skills_ranked or [],
        "skills_emphasized": emphasized or [],
    }


def test_experience_allocation_tracks_relevance():
    jd = "We need Kubernetes and Terraform experience for cloud infrastructure work."
    exps = [
        {"title": "Platform Engineer", "company": "A",
         "bullets": ["Ran Kubernetes clusters with Terraform for cloud infrastructure",
                     "Automated cloud infrastructure deployments with Terraform modules"]},
        {"title": "Barista", "company": "B", "bullets": ["Made espresso drinks"]},
    ]
    out = experience_allocation(_content(experiences=exps), jd)
    rows = out["experiences"]
    assert rows[0]["relevance"] > rows[1]["relevance"]
    assert rows[0]["words"] > rows[1]["words"]
    assert out["allocation_correlation"] == 1.0
    assert abs(sum(r["word_share"] for r in rows) - 1.0) < 0.01


def test_experience_allocation_empty_content():
    out = experience_allocation(_content(), "some jd text")
    assert out["experiences"] == []
    assert out["allocation_correlation"] is None
    assert out["total_bullet_words"] == 0


# ── skills metrics ─────────────────────────────────────────────────────────────

def test_skills_metrics_recall_and_selectivity():
    ranked = [
        {"name": "Python", "category": "Language", "score": 0.9},
        {"name": "Docker", "category": "Tool", "score": 0.5},
    ]
    matched = {"Python": {"match_type": "direct"}, "Kafka": {"match_type": "direct"}}
    out = skills_metrics(_content(skills_ranked=ranked), matched, total_profile_skills=10)
    assert out["rendered_count"] == 2
    assert out["selection_ratio"] == 0.2
    assert out["matched_recall"] == 0.5  # Python survived, Kafka did not
    assert out["categories"] == ["Language", "Tool"]


def test_skills_metrics_no_ranking():
    out = skills_metrics(_content(), {}, total_profile_skills=0)
    assert out["rendered_count"] == 0
    assert out["matched_recall"] is None
    assert out["within_cap_bounds"] is False


# ── redundancy ─────────────────────────────────────────────────────────────────

def test_redundancy_word_boundary_counting():
    # "SQL" must not be counted inside "MySQL" or "SQLAlchemy".
    exps = [{"title": "Eng", "company": "A",
             "bullets": ["Used MySQL and SQLAlchemy daily", "Wrote SQL queries"]}]
    ranked = [{"name": "SQL", "category": "Language", "score": 1.0}]
    out = redundancy_metrics(_content(experiences=exps, skills_ranked=ranked))
    assert out["term_counts"]["sql"] == 2  # skills section + one bullet


def test_redundancy_flags_over_repeated_terms():
    bullets = [f"Improved Python service number {i} with Python" for i in range(3)]
    exps = [{"title": "Eng", "company": "A", "bullets": bullets}]
    ranked = [{"name": "Python", "category": "Language", "score": 1.0}]
    out = redundancy_metrics(_content(experiences=exps, skills_ranked=ranked))
    assert out["term_counts"]["python"] > OVER_REPEAT_THRESHOLD
    assert "python" in out["over_repeated"]
    assert out["over_repeated_count"] == 1


def test_redundancy_falls_back_to_skills_emphasized():
    exps = [{"title": "Eng", "company": "A", "bullets": ["Shipped Python code"]}]
    out = redundancy_metrics(_content(experiences=exps, emphasized=["Python"]))
    assert out["term_counts"]["python"] == 2  # bullet + emphasized list rendered as skills


def test_redundancy_suite_is_additive_over_the_original_keys():
    """#122 merges four new modes in; every pre-existing key keeps its meaning.

    The notebook (`tailoring_benchmark.ipynb`) and `_aggregate` both read
    `over_repeated_count` and `bullet_type_token_ratio`, so this is a contract
    with live consumers, not just a naming convention.
    """
    bullets = [f"Improved Python service number {i} with Python" for i in range(3)]
    exps = [{"title": "Eng", "company": "A", "bullets": bullets}]
    ranked = [{"name": "Python", "category": "Language", "score": 1.0}]
    out = redundancy_metrics(_content(experiences=exps, skills_ranked=ranked))

    for key in ("term_counts", "max_term_repetition", "mean_term_repetition",
                "over_repeated", "over_repeated_count", "bullet_type_token_ratio"):
        assert key in out, f"pre-#122 key {key} disappeared"
    # The new modes are present alongside them.
    for key in ("bullet_df", "max_bullet_df", "leading_verb_entropy", "mtld",
                "mean_new_information", "bullet_count"):
        assert key in out, f"#122 key {key} missing"
    # Semantic keys stay out unless an encoder is supplied (stub runs pass none).
    assert "max_pairwise_cosine" not in out


def test_benchmark_supplies_no_encoder_in_plumbing_mode():
    """Stub vectors are hash-derived and carry no semantic relation, so a
    cosine computed from them would be stable and meaningless (#122/#158)."""
    from eval.tailoring_benchmark import MODE_PLUMBING, _semantic_encoder

    assert _semantic_encoder(MODE_PLUMBING) is None


@pytest.mark.parametrize("mode", ["product", "replay"])
def test_a_missing_encoder_fails_the_run_rather_than_disabling_semantics(mode, monkeypatch):
    """`_semantic_encoder` is keyed on the mode, not on encoder availability.

    Returning None here would leave semantic duplication silently unmeasured in
    replay — quietly re-creating #122's symptom (all four redundancy modes
    reporting clean) inside the mode built to fix it.
    """
    import agents.matcher as matcher

    from eval.tailoring_benchmark import _semantic_encoder

    monkeypatch.setattr(matcher, "get_embedding_model",
                        lambda: (_ for _ in ()).throw(ImportError("no model")))
    with pytest.raises(SystemExit, match="needs the real embedding model"):
        _semantic_encoder(mode)


# ── ATS summary ────────────────────────────────────────────────────────────────

def test_ats_summary_deltas():
    baseline = {"composite": 40.0, "skill_coverage": {"score": 30.0},
                "keyword_coverage": {"score": 20.0}, "section_presence": {"score": 100.0},
                "role_level": {"score": 50.0}}
    tailored = {"composite": 70.0, "skill_coverage": {"score": 80.0},
                "keyword_coverage": {"score": 45.0}, "section_presence": {"score": 100.0},
                "role_level": {"score": 50.0}}
    out = ats_summary(baseline, tailored)
    assert out["delta"] == 30.0
    assert out["skill_coverage"]["delta"] == 50.0
    assert out["section_presence"]["delta"] == 0.0


def test_ats_summary_missing_breakdowns():
    out = ats_summary({}, {})
    assert out["delta"] is None


# ── stub determinism ───────────────────────────────────────────────────────────

def test_stub_jd_skill_extraction_is_deterministic_and_jd_sensitive():
    from eval.tailoring_benchmark import _stub_extract_jd_skills

    jd = "Looking for Python and Kubernetes engineers. TensorFlow is a plus."
    a, b = _stub_extract_jd_skills(jd), _stub_extract_jd_skills(jd)
    assert a == b
    names = {s["name"] for s in a}
    assert {"Python", "Kubernetes", "TensorFlow"} <= names
    assert "Unity" not in names


def test_stub_embedding_model_is_stable_across_processes():
    """The stub embedder must not depend on Python's per-process hash seed.

    It seeded numpy from `hash(text)`, which PEP 456 randomizes per process, so
    every benchmark run drew different "embeddings" for the same skill name.
    Since 'semantic' carries the largest single weight in skill_scorer.WEIGHTS,
    that alone made `skills_rendered` unreproducible while every lexical metric
    stayed bit-identical — the headline symptom of issue #158.

    Forcing three *different* PYTHONHASHSEED values makes this decisive rather
    than probabilistic: under the old code the three outputs always differ.
    """
    code = (
        "import numpy as np;"
        "from eval.tailoring_benchmark import _StubEmbeddingModel;"
        "v = _StubEmbeddingModel().encode(['Python', 'FastAPI', 'PostgreSQL']);"
        "print(np.asarray(v).round(10).tolist())"
    )
    outs = set()
    for seed in ("0", "1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT, capture_output=True, text=True, timeout=120, env=env,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        outs.add(proc.stdout.strip())
    assert len(outs) == 1, f"stub embeddings vary with PYTHONHASHSEED: {len(outs)} distinct"


def test_stub_llm_round_trips_through_langchain_chain():
    from langchain_core.output_parsers import JsonOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    from eval.tailoring_benchmark import _make_stub_llm

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a job description parser. Extract the job title and company name."),
        ("user", "Text:\n{text}"),
    ])
    chain = prompt | _make_stub_llm()(role="extract") | JsonOutputParser()
    out = chain.invoke({"text": "whatever"})
    assert out == {"title": "Benchmark Role", "company": "Benchmark Co"}


# ── dataset sanity ─────────────────────────────────────────────────────────────

def test_jd_dataset_is_present_and_well_formed():
    files = sorted((ROOT / "eval" / "jd_dataset").glob("*.json"))
    assert len(files) >= 5, "checked-in JD dataset went missing"
    for path in files:
        task = json.loads(path.read_text(encoding="utf-8"))
        for key in ("id", "company", "title", "description", "source", "url"):
            assert key in task, f"{path.name} missing {key}"
        assert len(task["description"]) > 500


# ── plumbing mode never rewrites a bullet (issue #171) ────────────────────────

def test_plumbing_mode_returns_source_bullets_verbatim():
    """The canned payload passes every source bullet through unchanged.

    This is the defect #171 exists to name: `--stub` numbers measure the harness
    and the deterministic post-processing, never the rewrite. The property is
    pinned here so it can never again be mistaken for tailoring — and so that a
    later change which makes the stub *simulate* rewriting fails loudly rather
    than quietly restoring a plausible number that measures nothing.
    """
    from eval.tailoring_benchmark import _stub_tailored, fixture

    profile = fixture()
    out = _stub_tailored("Senior Python engineer: FastAPI, PyTorch, Kafka, AWS.")
    assert [e["bullets"] for e in out["experiences"]] == \
           [e["bullets"] for e in profile.experiences]
    assert [p["bullets"] for p in out["projects"]] == \
           [p["bullets"] for p in profile.projects]


def test_every_mode_declares_what_it_may_claim():
    from eval.tailoring_benchmark import MODE_CLAIMS, MODES

    assert set(MODES) == set(MODE_CLAIMS)
    assert len(MODES) == 3


def test_an_unknown_mode_is_rejected_before_anything_runs():
    from eval.tailoring_benchmark import run_benchmark

    with pytest.raises(SystemExit, match="unknown mode"):
        run_benchmark(mode="stub")  # the boolean's old name is not a mode


def test_recording_is_refused_outside_product_mode():
    """A cassette recorded from canned payloads would replay the stub."""
    from eval.tailoring_benchmark import MODE_PLUMBING, run_benchmark

    with pytest.raises(SystemExit, match="only applies to --mode product"):
        run_benchmark(mode=MODE_PLUMBING, record=True)


def test_plumbing_mode_is_labelled_as_not_measuring_quality():
    """The mode label must state the limitation, not just name the mode."""
    from eval.tailoring_benchmark import MODE_CLAIMS, MODE_PLUMBING, MODE_PRODUCT

    plumbing = MODE_CLAIMS[MODE_PLUMBING].lower()
    assert "not tailoring quality" in plumbing
    assert "verbatim" in plumbing
    assert "tailoring quality" in MODE_CLAIMS[MODE_PRODUCT].lower()


# ── end-to-end smoke (real web API, stub LLM, subprocess isolation) ────────────

@pytest.fixture(scope="module")
def plumbing_run(tmp_path_factory):
    """One `--stub --limit 1` benchmark run, shared by the tests below.

    Module-scoped because it drives the whole web API in a subprocess; the
    render-level assertions below would otherwise pay for a second full run.
    """
    out_dir = tmp_path_factory.mktemp("plumbing_run")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "eval" / "tailoring_benchmark.py"),
         "--stub", "--limit", "1", "--out", str(out_dir)],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, f"benchmark failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    results_files = list(out_dir.glob("tailoring_benchmark_*.json"))
    assert len(results_files) == 1
    return {
        "out_dir": out_dir,
        "stdout": proc.stdout,
        "results": json.loads(results_files[0].read_text(encoding="utf-8")),
    }


def test_benchmark_end_to_end_stub_smoke(plumbing_run):
    """One task through register→ingest→analyze→tailor→export via the API."""
    tmp_path = plumbing_run["out_dir"]
    results = plumbing_run["results"]
    assert results["mode"] == "plumbing"
    assert results["failed"] == []
    task = results["task_results"][0]
    assert task["mode"] == "plumbing"
    m = task["metrics"]
    # The tailored composite must exist and beat (or match) baseline in stub mode.
    assert m["ats"]["tailored_composite"] is not None
    assert m["ats"]["delta"] is not None and m["ats"]["delta"] >= 0
    # All three quality families computed.
    assert m["experience_allocation"]["experiences"]
    assert m["skills"]["rendered_count"] > 0
    assert m["redundancy"]["term_counts"]
    # CSV artifact alongside the JSON.
    assert list(tmp_path.glob("tailoring_benchmark_*.csv"))
    # Rendered .tex + raw content written for the notebook's resume viewer.
    renders = list((tmp_path / "renders").rglob("*.tex"))
    assert renders and renders[0].read_text(encoding="utf-8").startswith("%----")


def test_plumbing_run_renders_only_verbatim_source_bullets(plumbing_run):
    """End-to-end proof of the same property, at the artifact the metrics read.

    Post-processing legitimately *drops* bullets (bullet budget, one-page
    fitting), so this asserts containment rather than equality — but every
    surviving bullet must be byte-identical to one in the ingested fixture. If
    a single rendered bullet is not, plumbing mode has started rewriting and
    every claim made about the mode needs re-reading.
    """
    from eval.tailoring_benchmark import fixture

    source = set(fixture().bullets)
    renders = list((plumbing_run["out_dir"] / "renders").rglob("*.json"))
    assert renders, "no rendered content written"

    rendered_total = 0
    for path in renders:
        content = json.loads(path.read_text(encoding="utf-8"))
        for item in (*content.get("experiences", []), *content.get("projects", [])):
            for bullet in item.get("bullets", []):
                rendered_total += 1
                assert bullet in source, (
                    f"{path.name}: rendered bullet is not verbatim from the "
                    f"fixture — plumbing mode is rewriting: {bullet!r}"
                )
    assert rendered_total > 0, "no bullets rendered at all"


def test_run_prints_its_execution_mode(plumbing_run):
    """The console output a developer actually reads carries the caveat."""
    assert "EXECUTION MODE: PLUMBING" in plumbing_run["stdout"]
    assert "NOT tailoring quality" in plumbing_run["stdout"]


# ── replay mode against the committed cassette ────────────────────────────────

def test_committed_cassette_is_well_formed_and_covers_every_task():
    """Cheap structural check of the recording the replay leg depends on.

    Deliberately not a replay run: replay needs the real sentence-transformers
    model, so it belongs in the integration leg below rather than in the fast
    suite. This still catches a truncated, re-keyed or partially-recorded
    cassette landing in the repo.
    """
    from eval.cassettes import Cassette, SETUP_SCOPE, interaction_key
    from eval.tailoring_benchmark import DEFAULT_PROFILE, default_cassette_path

    path = default_cassette_path(DEFAULT_PROFILE.stem, 3, None)
    cassette = Cassette.load(path)
    assert len(cassette) > 0
    # The register/ingest phase plus one scope per recorded task.
    scopes = cassette.scopes()
    assert scopes[0] == SETUP_SCOPE
    assert set(scopes[1:]) == set(cassette.meta["tasks"])
    # Every entry is addressable by the key its fields imply, and occurrences
    # within a (scope, role, prompt) run 0..n-1 with no gaps.
    seen = {}
    for entry in cassette.entries():
        key = interaction_key(entry["scope"], entry["role"],
                              entry["prompt_sha256"], entry["occurrence"])
        assert cassette.get(key) is entry
        counter = (entry["scope"], entry["role"], entry["prompt_sha256"])
        seen.setdefault(counter, []).append(entry["occurrence"])
        assert entry["surface"] in ("invoke", "structured")
    for counter, occurrences in seen.items():
        assert sorted(occurrences) == list(range(len(occurrences))), counter


@pytest.mark.integration
@pytest.mark.slow
def test_two_replays_are_byte_identical(tmp_path):
    """#158's determinism property, carried into the new mode.

    Runs the committed cassette twice and diffs the metrics and the rendered
    artifacts. Integration-marked because replay uses the *real* embedding
    model — semantic redundancy is not stubbed out in replay, by design.
    """
    from eval.cassettes import Cassette
    from eval.tailoring_benchmark import DATASET_DIR, DEFAULT_PROFILE, default_cassette_path

    # Driven by the cassette's *own* task list rather than `--limit 3`.
    # `--limit` takes an alphabetical prefix of whatever is in the dataset, so a
    # corpus change silently re-points this test at tasks the recording never
    # covered — which surfaces as a bare CASSETTE MISS rather than as the real
    # problem (issue #177 replaced the corpus wholesale).
    recorded = Cassette.load(default_cassette_path(DEFAULT_PROFILE.stem, 3, None)
                             ).meta["tasks"]
    available = {p.stem for p in DATASET_DIR.glob("*.json")}
    missing = [t for t in recorded if t not in available]
    if missing:
        pytest.skip(
            "the committed cassette predates the current JD corpus "
            f"(missing: {', '.join(missing)}). Re-record with "
            "`--mode product --record --limit 3` once the profile set is final "
            "(#172) — recording before the fixture stops changing pays twice.")

    runs = []
    for i in (1, 2):
        out = tmp_path / f"run{i}"
        proc = subprocess.run(
            [sys.executable, str(ROOT / "eval" / "tailoring_benchmark.py"),
             "--mode", "replay", "--tasks", *recorded, "--out", str(out)],
            cwd=ROOT, capture_output=True, text=True, timeout=1800,
        )
        assert proc.returncode == 0, f"replay {i} failed:\n{proc.stderr[-3000:]}"
        assert "CASSETTE MISS" not in proc.stderr
        results = json.loads(
            sorted(out.glob("tailoring_benchmark_*.json"))[-1].read_text(
                encoding="utf-8"))
        results.pop("timestamp")
        for task in results["task_results"]:
            task.pop("job_id", None)  # a fresh uuid4 PK, not a metric
        renders = {p.name: p.read_bytes()
                   for p in sorted((out / "renders").rglob("*.tex"))}
        runs.append((results, renders))

    assert runs[0][0] == runs[1][0], "replayed metrics are not reproducible"
    assert runs[0][1] == runs[1][1], "replayed renders are not byte-identical"
    # Replay must actually measure semantic duplication — the metric plumbing
    # mode structurally cannot report (issue #122's headline gap).
    assert runs[0][0]["aggregate"]["max_pairwise_cosine"] is not None


# ── LLM-as-judge quality scoring (issue #27's aim, applied to tailoring) ──────

def _fake_judge_llm(payload):
    """Runnable standing in for the judge model."""
    import json as _json

    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda
    return RunnableLambda(lambda _pv: AIMessage(content=_json.dumps(payload)))


def test_llm_judge_parses_scores_and_means():
    from eval.llm_judge import judge_resume_quality

    payload = {
        "relevance_balance": {"score": 4, "rationale": "top exp detailed"},
        "redundancy": {"score": 3, "rationale": "python repeated"},
        "faithfulness": {"score": 5, "rationale": "all claims sourced"},
    }
    out = judge_resume_quality(_content(), "jd", "profile", llm=_fake_judge_llm(payload))
    assert out["mean_score"] == 4.0
    assert out["redundancy"]["score"] == 3


@pytest.mark.parametrize("payload", [
    {"relevance_balance": {"score": 9, "rationale": "x"},
     "redundancy": {"score": 3, "rationale": "x"},
     "faithfulness": {"score": 3, "rationale": "x"}},          # out of range
    {"relevance_balance": {"rationale": "no score"},
     "redundancy": {"score": 3, "rationale": "x"},
     "faithfulness": {"score": 3, "rationale": "x"}},          # missing score
    {"redundancy": {"score": 3, "rationale": "x"}},            # missing axis
])
def test_llm_judge_rejects_malformed_output(payload):
    from eval.llm_judge import judge_resume_quality

    assert judge_resume_quality(_content(), "jd", "profile", llm=_fake_judge_llm(payload)) is None


@pytest.mark.integration
def test_llm_judge_scores_real_resume():
    """End-to-end judge call against the real eval model (needs API keys)."""
    from eval.llm_judge import judge_resume_quality
    from eval.tailoring_benchmark import DEFAULT_PROFILE, fixture

    profile = fixture()
    content = {
        "experiences": profile.experiences,
        "projects": [{"name": p["name"], "bullets": p["bullets"]} for p in profile.projects],
        "skills_emphasized": ["Python", "PyTorch", "FastAPI"],
    }
    jd = "Machine Learning Engineer role: PyTorch, model serving, feature stores, AWS."
    out = judge_resume_quality(content, jd, DEFAULT_PROFILE.read_text(encoding="utf-8"))
    assert out is not None
    assert 1 <= out["mean_score"] <= 5


# ── per-stratum aggregation (issue #172) ──────────────────────────────────────

def _task_row(task_id, family, level="entry", delta=10.0, attempts=None):
    return {
        "task_id": task_id, "mode": "plumbing",
        "role_family": family, "level": level, "n_attempts": attempts,
        "metrics": {"ats": {"delta": delta, "baseline_composite": 50.0,
                            "tailored_composite": 50.0 + delta},
                    "experience_allocation": {}, "skills": {}, "redundancy": {}},
    }


def test_pooled_aggregate_is_unchanged_by_the_stratum_split():
    """The pooled figure must survive verbatim.

    Every historical table in CHANGELOG.md is pooled and #171 retro-labelled
    rather than deleted them; replacing the pooled number would break
    comparability with everything already recorded.
    """
    from eval.tailoring_benchmark import _aggregate

    rows = [_task_row("a", "data_science", delta=10.0),
            _task_row("b", "ml_engineering", delta=30.0)]
    assert _aggregate(rows)["ats_delta"]["mean"] == 20.0
    assert _aggregate(rows)["tasks"] == 2


def test_aggregate_by_stratum_slices_every_axis():
    from eval.tailoring_benchmark import _aggregate_by_stratum

    rows = [_task_row("a", "data_science", "entry", 10.0),
            _task_row("b", "data_science", "intern", 20.0),
            _task_row("c", "ml_engineering", "entry", 40.0)]
    by = _aggregate_by_stratum(rows)
    assert by["role_family"]["data_science"]["ats_delta"]["mean"] == 15.0
    assert by["role_family"]["ml_engineering"]["ats_delta"]["mean"] == 40.0
    assert by["level"]["entry"]["ats_delta"]["mean"] == 25.0
    assert by["level"]["intern"]["tasks"] == 1


def test_stratum_values_are_sorted_so_the_table_cannot_reorder():
    """#158/#171: a table that reorders between runs is the same bug class."""
    from eval.tailoring_benchmark import _aggregate_by_stratum

    rows = [_task_row("a", "software_engineering"), _task_row("b", "ai_engineering"),
            _task_row("c", "data_science")]
    forward = list(_aggregate_by_stratum(rows)["role_family"])
    backward = list(_aggregate_by_stratum(list(reversed(rows)))["role_family"])
    assert forward == backward == sorted(forward)


def test_a_task_missing_one_axis_is_skipped_only_for_that_axis():
    from eval.tailoring_benchmark import _aggregate_by_stratum

    rows = [_task_row("a", "data_science", "entry"), _task_row("b", None, "entry")]
    by = _aggregate_by_stratum(rows)
    assert by["role_family"]["data_science"]["tasks"] == 1
    assert by["level"]["entry"]["tasks"] == 2


def test_an_axis_no_task_declares_is_omitted_entirely():
    from eval.tailoring_benchmark import _aggregate_by_stratum

    rows = [_task_row("a", None, None)]
    assert _aggregate_by_stratum(rows) == {}


def test_n_attempts_is_reported_because_the_delta_is_a_max_over_it():
    """Best-of-N is on by default and N is endogenous, so `ats_delta` is a max
    over a data-dependent number of draws. The count was already written to
    tailoring_decisions and simply never read."""
    from eval.tailoring_benchmark import _aggregate

    rows = [_task_row("a", "data_science", attempts=1),
            _task_row("b", "data_science", attempts=2)]
    assert _aggregate(rows)["n_attempts"]["mean"] == 1.5
    assert _aggregate(rows)["n_attempts"]["max"] == 2.0


def test_n_attempts_is_null_when_no_task_recorded_one():
    from eval.tailoring_benchmark import _aggregate

    assert _aggregate([_task_row("a", "data_science")])["n_attempts"] is None


def test_stratum_columns_reach_the_csv():
    """A CSV row is where someone re-pools the numbers without the stratum."""
    from eval.tailoring_benchmark import _CSV_COLUMNS

    names = [name for name, _ in _CSV_COLUMNS]
    for required in ("mode", "role_family", "level", "n_attempts"):
        assert required in names, required
