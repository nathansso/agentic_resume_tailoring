"""
Tailoring efficacy benchmark (issue #51, Phase 1).

Runs the checked-in JD dataset (eval/jd_dataset/) through the tailoring
pipeline **via the web API, exactly as a user would**: register → login →
upload resume → create job → paste description → Analyze → Tailor → Export.
Everything goes through FastAPI routes on an isolated temp database — the
production DB, the local ~/.art profile pointer, and the deployed site are
never touched.

Per task it computes the metric families in eval/metrics.py (ATS baseline →
tailored delta, experience-allocation balance, skills organization, term
redundancy) and writes:

    eval/results/tailoring_benchmark_<ts>.json   # full per-task + aggregate
    eval/results/tailoring_benchmark_<ts>.csv    # flat per-task table
    eval/results/renders/<ts>/<task>.tex|.json   # rendered resume + raw content

Execution modes (issue #171) — see MODE_CLAIMS for what each may claim:

    --mode product    real LLM + real embeddings, the deployed path. The only
                      mode whose numbers describe tailoring quality.
    --mode replay     recorded model responses, everything else real. What the
                      recording covered, deterministically, at ~zero cost.
    --mode plumbing   canned payloads (the old --stub). Wiring, schemas and
                      determinism only — never tailoring quality.

    python eval/tailoring_benchmark.py --mode plumbing --limit 3
    python eval/tailoring_benchmark.py --mode product --record --limit 3
    python eval/tailoring_benchmark.py --mode replay --limit 3
    python eval/tailoring_benchmark.py --tasks stripe_ai_engineer duolingo_software_engineer_i

Replay contract (what a replay run does and does not reproduce) is documented
in eval/README.md.

The notebook eval/tailoring_benchmark.ipynb drives this module and visualizes
the artifacts.
"""
import argparse
import csv
import json
import os
import sys
import tempfile
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.cassettes import (  # noqa: E402
    MODE_RECORD as CASSETTE_RECORD,
    MODE_REPLAY as CASSETTE_REPLAY,
    Cassette,
    CassetteSession,
    default_cassette_path,
    recording_meta,
    render_prompt,
)
from eval.profile_fixture import ProfileFixture, load_profile  # noqa: E402

DATASET_DIR = ROOT / "eval" / "jd_dataset"
RESULTS_DIR = ROOT / "eval" / "results"
DEFAULT_PROFILE = ROOT / "eval" / "profiles" / "benchmark_profile.md"

BENCH_EMAIL = "benchmark@example.com"
BENCH_PASSWORD = "benchmark-pass-123"


# ── execution modes (issue #171) ───────────────────────────────────────────────
#
# One harness, three modes with different evidentiary weight. The mode travels
# with every number this file emits — the run banner, the console summary, the
# per-task rows, and the persisted JSON/CSV — because the defect #171 exists to
# remove was not a wrong number, it was a correct number read as a claim it
# could not support.

MODE_PRODUCT = "product"
MODE_REPLAY = "replay"
MODE_PLUMBING = "plumbing"
MODES = (MODE_PRODUCT, MODE_REPLAY, MODE_PLUMBING)

MODE_CLAIMS = {
    MODE_PRODUCT:
        "Real LLM + real embeddings, the deployed path. Tailoring quality — "
        "the only mode whose numbers describe the product.",
    MODE_REPLAY:
        "Recorded LLM responses, real embeddings, every deterministic line of "
        "the pipeline executing for real. Everything the recording covered, "
        "deterministically, at near-zero marginal cost.",
    MODE_PLUMBING:
        "Canned payloads, no model. Wiring, schemas and determinism ONLY — "
        "explicitly NOT tailoring quality: the stub returns every source bullet "
        "verbatim, so no bullet is ever rewritten and no metric here can move "
        "with the quality of a rewrite.",
}


# ── environment isolation (must run before any project import) ────────────────

def _prepare_environment(workdir: Path) -> None:
    """
    Point every stateful surface at the temp workdir and force the offline
    local-cookie auth mode. config.py reads these at import time, so this must
    run before web.app / database.db are imported.

    The database is Postgres when ART_TEST_DATABASE_URL is set (issue #149) so
    benchmark numbers are measured on production storage semantics, and a
    temp-file SQLite database otherwise.
    """
    from eval.eval_db import make_throwaway_db

    os.environ["DATABASE_URL"] = make_throwaway_db("benchmark", workdir)
    os.environ["ART_DATA_DIR"] = str(workdir)
    os.environ["AI_DAILY_LIMIT"] = "10000"  # the benchmark legitimately batches AI calls
    for var in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_JWT_SECRET"):
        os.environ.pop(var, None)


def _patch_profile_pointer(workdir: Path) -> None:
    """user_utils hardcodes ~/.art; rebind it. (Routers no longer import the
    pointer names — web requests bind the acting user per-context, issue #73.)"""
    import database.user_utils as user_utils

    art_dir = workdir / ".art"
    art_dir.mkdir(parents=True, exist_ok=True)
    pointer = art_dir / "active_profile_id"
    user_utils.ART_DIR = art_dir
    user_utils.ACTIVE_PROFILE_FILE = pointer


# ── deterministic stub LLM (offline mode) ──────────────────────────────────────
#
# The canned parse is *derived from the profile fixture* (issue #171), not
# hand-copied from it. It used to be three module-level constants whose own
# comment said they were "kept aligned with the fixture" — a duplicate of the
# one profile that exists, which had already drifted (30 skills against the
# fixture's 32) and which made a second profile impossible without a second
# hand-written parse. eval/profile_fixture.py parses the markdown instead, so
# any fixture in the same shape works with no Python change. See #172.

_FIXTURE: Optional["ProfileFixture"] = None


def fixture() -> "ProfileFixture":
    """The parsed profile the canned payloads are derived from.

    Defaults to DEFAULT_PROFILE so importing this module (tests, the notebook)
    needs no run in progress; `bind_fixture` rebinds it per benchmark run.
    """
    global _FIXTURE
    if _FIXTURE is None:
        _FIXTURE = load_profile(DEFAULT_PROFILE)
    return _FIXTURE


def bind_fixture(profile_path: Path) -> "ProfileFixture":
    """Point the canned payloads at the profile this run actually ingests."""
    global _FIXTURE
    _FIXTURE = load_profile(profile_path)
    return _FIXTURE


# Terms the JD-skill "analyzer" scans for beyond the profile's own skills, so
# that missing_skills is non-empty on a realistic posting.
_EXTRA_JD_TERMS = [
    "Java", "Go", "Rust", "Scala", "GraphQL", "Spark", "TensorFlow", "Terraform",
    "GCP", "Azure", "MongoDB", "Elasticsearch", "machine learning", "deep learning",
    "LLM", "microservices", "REST", "CI/CD",
]


def stub_jd_vocab() -> List[str]:
    """Vocabulary the stub analyzer scans job text against: this profile's
    skills plus common terms it lacks."""
    return [s["name"] for s in fixture().skills] + _EXTRA_JD_TERMS


def _stub_extract_jd_skills(jd_text: str) -> List[Dict]:
    low = jd_text.lower()
    out = []
    for name in stub_jd_vocab():
        idx = low.find(name.lower())
        if idx == -1:
            continue
        out.append({
            "name": name,
            "category": "Tool",
            # deterministic proxy for prominence: earlier mention → heavier weight
            "required": idx < len(low) / 2,
            "weight": round(max(0.1, 1.0 - idx / max(len(low), 1)), 2),
        })
    return out


# Section headings that tell the stub how to type a requirement. Order matters:
# the first marker found in the current heading wins.
_STUB_SECTION_TYPES = [
    (("nice to have", "preferred", "bonus", "plus", "desirable"), "preferred"),
    (("about", "benefit", "perk", "equal opportunity", "who we are",
      "compensation", "salary"), "incidental"),
    (("requirement", "qualification", "must", "you will", "you'll",
      "responsibilit", "what you", "skill"), "required"),
]
_STUB_MAX_REQUIREMENTS = 40


def _stub_jd_profile(prompt_text: str) -> Dict:
    """Deterministic stand-in for the #121 JD-profile extraction.

    Without this the stub returns `{}` for the profile prompt, every posting
    compiles to zero requirements, and #125's importance half is never
    exercised by the benchmark — only its supportability half would be
    measured. Derives requirements from the posting's own bullet lines, in
    source order, so `type` / `criticality` / `source_section` / `ordinal` all
    carry real (if simple) structure.
    """
    from agents.ats_scorer import ATSScoringEngine

    title = ""
    body = prompt_text
    for line in prompt_text.splitlines():
        if line.startswith("Job title:"):
            title = line.split(":", 1)[1].strip()
            break
    marker = "Job posting:"
    if marker in prompt_text:
        body = prompt_text.split(marker, 1)[1]

    vocab = sorted({v.lower() for v in stub_jd_vocab()})
    requirements: List[Dict] = []
    section = ""
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        stripped = line.lstrip("-•*• ").strip()
        is_bullet = line[0] in "-•*•"
        if not is_bullet:
            # Short, unpunctuated lines read as headings.
            if len(line) < 80 and not line.endswith("."):
                section = line
            continue
        if len(stripped) < 15 or len(requirements) >= _STUB_MAX_REQUIREMENTS:
            continue

        low_section = section.lower()
        rtype = "required"
        for markers, value in _STUB_SECTION_TYPES:
            if any(m in low_section for m in markers):
                rtype = value
                break

        low = stripped.lower()
        terms = [v for v in vocab if v in low]
        if not terms:
            terms = sorted(ATSScoringEngine._extract_keywords(stripped),
                           key=lambda t: (-len(t), t))[:3]
        requirements.append({
            "text": stripped,
            "type": rtype,
            # Earlier requirements are more central; saturates at 1.
            "criticality": max(1, 5 - len(requirements) // 4),
            "terms": terms,
            "source_section": section or None,
            "confidence": 0.9,
        })

    return {
        "requirements": requirements,
        "title_terms": sorted(ATSScoringEngine._extract_keywords(title)),
    }


def _stub_tailored(jd_text: str) -> Dict:
    """Canned "tailoring": source bullets, unchanged, plus a substring-matched
    skills list.

    **This never rewrites a bullet, and that is deliberate** (issue #171). Every
    experience and project bullet is returned byte-identical to the ingested
    fixture, so a plumbing run measures the harness and the deterministic
    post-processing — bullet budget, one-page fitting, ordering, skill selection
    — and nothing about the rewrite the product's LLM actually performs. The
    property is pinned by tests so it can never again be mistaken for tailoring.

    Making the stub *simulate* rewriting would reintroduce exactly the defect
    #171 removes: a plausible number measuring nothing. It stays honest and
    narrow; `--mode product` and `--mode replay` are where tailoring is measured.
    """
    profile = fixture()
    low = jd_text.lower()
    emphasized = [s["name"] for s in profile.skills if s["name"].lower() in low]
    return {
        "experiences": [
            {k: e[k] for k in ("title", "company", "start_date", "end_date", "bullets")}
            for e in profile.experiences
        ],
        "projects": [
            {"name": p["name"], "selected_style": "technical", "bullets": p["bullets"]}
            for p in profile.projects
        ],
        "skills_emphasized": emphasized or [s["name"] for s in profile.skills[:8]],
    }


def _stub_payload(text: str):
    """Route a formatted prompt to its canned/deterministic payload (dict/list).

    Anything with no branch here (education, achievements) returns `{}` and so
    compiles to an empty list — the same surface the constants covered before
    #171 derived them, deliberately unchanged so this stays a source swap rather
    than a re-baseline of what plumbing mode measures.
    """
    profile = fixture()
    if "Extract work experiences" in text:
        return profile.experiences
    if "Extract projects" in text:
        return [{k: p[k] for k in ("name", "description")} | (
            {"repo_url": p["repo_url"]} if "repo_url" in p else {})
            for p in profile.projects]
    if "Extract technical skills" in text:
        return profile.skills
    if "Extract the job title" in text:
        return {"title": "Benchmark Role", "company": "Benchmark Co"}
    if "job description analyzer" in text:
        return _stub_extract_jd_skills(text)
    if "decompose job postings" in text:
        return _stub_jd_profile(text)
    if "resume tailoring assistant" in text:
        return _stub_tailored(text)
    return {}


# The stub routes on prompt text and the cassette layer keys on it, so they must
# render a prompt the same way or a recording could not be matched to the run
# that produced it. One implementation, in eval/cassettes.py.
_prompt_text = render_prompt


def _make_stub_llm():
    """
    A drop-in for get_llm(): a Runnable that routes on distinctive prompt markers
    and returns canned/deterministic output, so the whole user flow runs offline
    through the real chains. Supports both surfaces the app now uses:
    - `prompt | llm | JsonOutputParser` (tailor/chat) via `.invoke` → AIMessage.
    - `llm.with_structured_output(Schema)` (the #142 extraction seam) → a
      validated Pydantic model, matching the real provider's behavior.
    """
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import Runnable, RunnableLambda

    def _to_schema(payload, schema):
        # Wrapper schemas hold a single list field (experiences/skills/…); flat
        # schemas (JobMetadata) take the dict directly.
        if isinstance(payload, list):
            key = next(iter(schema.model_fields))
            return schema(**{key: payload})
        if isinstance(payload, dict):
            return schema(**payload)
        return schema()

    class _StubLLM(Runnable):
        def invoke(self, input, config=None, **kwargs):
            return AIMessage(content=json.dumps(_stub_payload(_prompt_text(input))))

        def with_structured_output(self, schema, **kwargs):
            return RunnableLambda(
                lambda pv: _to_schema(_stub_payload(_prompt_text(pv)), schema)
            )

    return lambda role="chat", temperature=0.0: _StubLLM()


class _StubEmbeddingModel:
    """Deterministic hash-based embedder — no model download, stable vectors.

    The seed must come from a *stable* digest. Python randomizes `hash()` on
    str per process (PEP 456), so seeding from it gave every run a different
    vector for the same skill name. Since 'semantic' carries the largest single
    weight in skill_scorer.WEIGHTS (0.30), that made the rendered skills section
    unreproducible run to run while every lexical metric stayed bit-identical —
    the headline symptom of issue #158. blake2b is stable across processes,
    machines and Python versions, so this docstring's "stable vectors" claim is
    finally true without needing PYTHONHASHSEED=0.
    """

    def encode(self, texts, normalize_embeddings=True, **kwargs):
        import hashlib
        import numpy as np
        single = isinstance(texts, str)
        items = [texts] if single else list(texts)
        vecs = []
        for t in items:
            digest = hashlib.blake2b(t.lower().encode("utf-8"), digest_size=4).digest()
            rng = np.random.default_rng(int.from_bytes(digest, "big"))
            v = rng.standard_normal(32)
            v /= np.linalg.norm(v)
            vecs.append(v)
        arr = np.asarray(vecs)
        return arr[0] if single else arr


def _install_llm_factory(factory) -> None:
    """Patch the `get_llm` seam everywhere it was bound.

    Every model call resolves through `llm.get_llm`, so this one patch serves
    all three execution modes: the canned stub, a cassette recorder, and a
    cassette player (issue #171).

    agents/jd_profile.py builds its extractor through `llm.get_extractor`
    without passing an llm, so it never saw the module-level patches — its
    extraction failed and every posting silently compiled to no profile at all.
    Patching the factory itself covers that path and any future `get_extractor`
    caller (issue #125). agents/chat.py and agents/enhancer.py are patched
    despite the benchmark never driving chat: in replay mode an unpatched seam
    is a live provider call, and that must not be possible by accident.
    """
    import agents.chat as chat
    import agents.enhancer as enhancer
    import agents.job_analyzer as job_analyzer
    import agents.parser as parser
    import agents.tailor as tailor
    import llm as llm_module

    for module in (parser, job_analyzer, tailor, chat, enhancer):
        module.get_llm = factory
    llm_module.get_llm = factory


def _install_stub_embeddings() -> None:
    """Plumbing mode only: hash-derived vectors, no model download."""
    import agents.matcher as matcher

    matcher.get_embedding_model = lambda: _StubEmbeddingModel()
    matcher._embedding_model = None


def _install_stubs(profile_path: Path = DEFAULT_PROFILE) -> None:
    """Plumbing mode: canned payloads plus the hash embedder.

    Binds the canned payloads to the profile this run ingests, so the stub
    "parse" is always a parse of the fixture actually on disk (issue #171).
    """
    bind_fixture(profile_path)
    _install_llm_factory(_make_stub_llm())
    _install_stub_embeddings()


# ── dataset ────────────────────────────────────────────────────────────────────

def load_tasks(task_ids: Optional[List[str]] = None, limit: int = 0) -> List[Dict]:
    """The task set for one run, in a stable order.

    `limit` samples **across role families**, not off the front of the list. The
    corpus is stratified (issue #177: 30 postings in each of five families), and
    files sort alphabetically by company, so `tasks[:limit]` would hand back a
    dozen postings from whichever families happen to start with "A" — a
    stratified corpus sampled in a way that destroys the stratification, and
    silently, since every reported number would still look well-formed.

    Round-robins over families in a fixed order and takes them in sorted order
    within each, so the sample is balanced and reproducible (#158/#171).
    """
    tasks = []
    for path in sorted(DATASET_DIR.glob("*.json")):
        task = json.loads(path.read_text(encoding="utf-8"))
        if task_ids and task["id"] not in task_ids:
            continue
        tasks.append(task)
    if task_ids:
        missing = set(task_ids) - {t["id"] for t in tasks}
        if missing:
            raise SystemExit(f"Unknown task id(s): {', '.join(sorted(missing))}")
    if not limit or limit >= len(tasks):
        return tasks

    by_family: Dict[str, List[Dict]] = {}
    for task in tasks:
        by_family.setdefault(task.get("role_family") or "", []).append(task)

    sampled: List[Dict] = []
    families = sorted(by_family)
    depth = 0
    while len(sampled) < limit and any(len(by_family[f]) > depth for f in families):
        for family in families:
            if len(by_family[family]) > depth:
                sampled.append(by_family[family][depth])
                if len(sampled) == limit:
                    break
        depth += 1
    # Restored to corpus order so a run's task sequence stays independent of how
    # the sample was drawn — JobCard injection (#137) makes order load-bearing.
    order = {t["id"]: i for i, t in enumerate(tasks)}
    return sorted(sampled, key=lambda t: order[t["id"]])


# ── benchmark run ──────────────────────────────────────────────────────────────

def _api(client, method: str, url: str, **kwargs):
    resp = getattr(client, method)(url, **kwargs)
    if resp.status_code >= 400:
        raise RuntimeError(f"{method.upper()} {url} → {resp.status_code}: {resp.text[:500]}")
    return resp


def _semantic_encoder(mode: str):
    """Encoder for the redundancy suite's semantic metrics (issue #122/#171).

    Keyed on the execution *mode*, not on a `stub` boolean and not on whether
    `matcher.get_embedding_model()` happens to return something:

    * **plumbing** → None. The stub embedder's hash-derived vectors are
      deterministic but carry no semantic relation, so paraphrases look no more
      similar than unrelated sentences; a `max_pairwise_cosine` computed from
      them would be a stable, meaningless number dressed as a redundancy score
      — the failure mode #158 cleaned up.
    * **product / replay** → the real encoder, or a hard failure. Replay claims
      to run "every deterministic line of the pipeline for real", and semantic
      duplication is the one redundancy mode with a model dependency. Degrading
      to None here would silently disable it and quietly re-create #122's
      symptom (all four modes reporting clean) inside the new mode.
    """
    if mode == MODE_PLUMBING:
        return None
    try:
        from agents.matcher import get_embedding_model
        model = get_embedding_model()
    except Exception as exc:  # not installed / offline / OOM
        model = None
        reason = f"{type(exc).__name__}: {exc}"
    else:
        reason = "get_embedding_model() returned None"
    if model is None:
        raise SystemExit(
            f"--mode {mode} needs the real embedding model, and it is "
            f"unavailable ({reason}).\n"
            "Semantic duplication is the one redundancy metric with a model "
            "dependency; running without it would report the other three as "
            "clean and omit the fourth silently (issue #122/#171).\n"
            "Install it with: pip install -r requirements.txt"
        )
    return lambda texts: model.encode(texts, normalize_embeddings=True)


def _run_task(client, task: Dict, renders_dir: Path,
              judge: bool = False, profile_text: str = "",
              encoder=None, mode: str = MODE_PRODUCT) -> Dict:
    """Drive one JD through the exact user flow and compute its metrics."""
    from sqlmodel import Session, select

    from database.db import engine, latest_result
    from database.models import UserJobResult, UserSkill
    from eval.metrics import compute_task_metrics

    job = _api(client, "post", "/api/jobs/",
               json={"title": task["title"], "company": task["company"]}).json()
    job_id = job["job_id"]
    _api(client, "post", f"/api/jobs/{job_id}/description",
         json={"description": task["description"]})
    _api(client, "post", f"/api/jobs/{job_id}/analyze")
    _api(client, "post", f"/api/jobs/{job_id}/tailor")
    detail = _api(client, "get", f"/api/jobs/{job_id}").json()

    tex = _api(client, "get", f"/api/jobs/{job_id}/export?format=tex").text

    # Measurement (not user action): full tailored content + untruncated
    # matched skills + profile size, read straight from the isolated DB.
    from uuid import UUID
    with Session(engine) as session:
        results = session.exec(
            select(UserJobResult).where(UserJobResult.job_id == UUID(job_id))
        ).all()
        result = latest_result(results)
        tailored_content = result.tailored_resume_content or {}
        matched_skills = result.matched_skills or {}
        baseline_breakdown = result.score_breakdown or {}
        tailored_breakdown = result.tailored_score_breakdown or {}
        total_profile_skills = len({
            us.skill_id for us in session.exec(select(UserSkill)).all()
        })

    metrics = compute_task_metrics(
        tailored_content, task["description"], matched_skills,
        total_profile_skills, baseline_breakdown, tailored_breakdown,
        encoder=encoder,
    )
    if judge:
        # LLM-as-judge quality axes (issue #27's aim, applied to tailoring):
        # what the structural metrics can't see — how the resume *reads*.
        from eval.llm_judge import judge_resume_quality
        metrics["llm_judge"] = judge_resume_quality(
            tailored_content, task["description"], profile_text
        )

    (renders_dir / f"{task['id']}.tex").write_text(tex, encoding="utf-8")
    (renders_dir / f"{task['id']}.json").write_text(
        json.dumps(tailored_content, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return {
        "task_id": task["id"],
        # Carried per task, not only on the run: a task row read on its own —
        # in the CSV, in a notebook cell, pasted into an issue — must still say
        # which mode produced it (issue #171).
        "mode": mode,
        "company": task["company"],
        "title": task["title"],
        "job_id": job_id,
        "ats_score": detail.get("ats_score"),
        "metrics": metrics,
    }


def _install_mode(mode: str, profile_path: Path, cassette_path: Optional[Path],
                  record: bool, task_ids: List[str]):
    """Bind the `get_llm` seam for this mode. Returns the CassetteSession, if any.

    Product mode without `--record` patches nothing: it *is* the deployed path.
    """
    if mode == MODE_PLUMBING:
        _install_stubs(profile_path)
        return None
    if mode == MODE_REPLAY:
        session = CassetteSession(Cassette.load(cassette_path), CASSETTE_REPLAY)
        _install_llm_factory(session.llm_factory())
        return session
    if record:
        import llm as llm_module

        # Captured before the patch: the recorder must call the real factory,
        # not itself.
        session = CassetteSession(
            Cassette(meta=recording_meta(
                str(profile_path.relative_to(ROOT)), task_ids)),
            CASSETTE_RECORD, factory=llm_module.get_llm,
        )
        _install_llm_factory(session.llm_factory())
        return session
    return None


def _mode_banner(mode: str) -> str:
    """Framed, unmissable statement of what this run's numbers may claim."""
    rule = "─" * 78
    lines = [rule, f"  EXECUTION MODE: {mode.upper()}"]
    claim = MODE_CLAIMS[mode]
    line = "  "
    for word in claim.split():
        if len(line) + len(word) + 1 > 78:
            lines.append(line)
            line = "  "
        line += word + " "
    lines.append(line.rstrip())
    lines.append(rule)
    return "\n".join(lines)


def _aggregate(task_results: List[Dict]) -> Dict:
    def collect(path: List[str]) -> List[float]:
        vals = []
        for t in task_results:
            v: object = t["metrics"]
            for key in path:
                v = v.get(key) if isinstance(v, dict) else None
            if isinstance(v, (int, float)):
                vals.append(float(v))
        return vals

    def stats(vals: List[float]) -> Optional[Dict]:
        if not vals:
            return None
        return {"mean": round(mean(vals), 3), "median": round(median(vals), 3),
                "min": round(min(vals), 3), "max": round(max(vals), 3)}

    return {
        "tasks": len(task_results),
        "ats_delta": stats(collect(["ats", "delta"])),
        "baseline_composite": stats(collect(["ats", "baseline_composite"])),
        "tailored_composite": stats(collect(["ats", "tailored_composite"])),
        "allocation_correlation": stats(collect(["experience_allocation", "allocation_correlation"])),
        "skills_rendered": stats(collect(["skills", "rendered_count"])),
        "skills_matched_recall": stats(collect(["skills", "matched_recall"])),
        "skills_selection_ratio": stats(collect(["skills", "selection_ratio"])),
        "max_term_repetition": stats(collect(["redundancy", "max_term_repetition"])),
        "over_repeated_count": stats(collect(["redundancy", "over_repeated_count"])),
        "bullet_type_token_ratio": stats(collect(["redundancy", "bullet_type_token_ratio"])),
        # Issue #122's suite. `max_pairwise_cosine` is None on stub runs, where
        # no encoder is supplied; `stats` already returns None for an empty
        # collection, so the key is present-but-null rather than missing.
        "max_bullet_df": stats(collect(["redundancy", "max_bullet_df"])),
        "leading_verb_entropy": stats(collect(["redundancy", "leading_verb_entropy"])),
        "mtld": stats(collect(["redundancy", "mtld"])),
        "mean_new_information": stats(collect(["redundancy", "mean_new_information"])),
        "max_pairwise_cosine": stats(collect(["redundancy", "max_pairwise_cosine"])),
        "judge_mean_score": stats(collect(["llm_judge", "mean_score"])),
    }


def run_benchmark(
    task_ids: Optional[List[str]] = None,
    profile_path: Path = DEFAULT_PROFILE,
    mode: str = MODE_PRODUCT,
    limit: int = 0,
    out_dir: Path = RESULTS_DIR,
    workdir: Optional[Path] = None,
    judge: bool = False,
    cassette_path: Optional[Path] = None,
    record: bool = False,
) -> Dict:
    """Full benchmark run. Returns the results dict (also persisted to out_dir).

    `mode` is one of MODES. It is a mode, not a boolean, precisely because a
    third mode existed and the old `stub` flag had no room for it (issue #171).
    `record` snapshots a product run into `cassette_path` for later replay.
    """
    if mode not in MODES:
        raise SystemExit(f"unknown mode {mode!r}; expected one of {', '.join(MODES)}")
    if record and mode != MODE_PRODUCT:
        raise SystemExit("--record only applies to --mode product: a recording "
                         "must capture real model responses")
    tasks = load_tasks(task_ids, limit)
    if not tasks:
        raise SystemExit(f"No tasks found in {DATASET_DIR} — run scripts/scrape_job_descriptions.py")

    if mode == MODE_REPLAY or record:
        cassette_path = Path(cassette_path or default_cassette_path(
            profile_path.stem, limit, task_ids))
    print(_mode_banner(mode), flush=True)
    if cassette_path:
        print(f"  cassette: {cassette_path}", flush=True)

    own_tmp = None
    if workdir is None:
        own_tmp = tempfile.TemporaryDirectory(prefix="art_benchmark_")
        workdir = Path(own_tmp.name)
    _prepare_environment(workdir)

    try:
        from fastapi.testclient import TestClient

        from database.db import init_db
        from web.app import create_app

        _patch_profile_pointer(workdir)
        session = _install_mode(mode, profile_path, cassette_path, record,
                                [t["id"] for t in tasks])
        init_db()

        client = TestClient(create_app())

        # The exact onboarding a user performs.
        _api(client, "post", "/api/auth/register", json={
            "name": "Benchmark User", "email": BENCH_EMAIL,
            "username": "benchmark", "password": BENCH_PASSWORD,
        })
        with open(profile_path, "rb") as fh:
            _api(client, "post", "/api/ingest/resume",
                 files={"file": (profile_path.name, fh, "text/markdown")})

        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        renders_dir = out_dir / "renders" / ts
        renders_dir.mkdir(parents=True, exist_ok=True)

        profile_text = profile_path.read_text(encoding="utf-8")
        # Resolved once: the model load is cached in matcher, but the redundancy
        # suite's encoder is a per-run property, not a per-task one (issue #122).
        encoder = _semantic_encoder(mode)
        task_results = []
        for i, task in enumerate(tasks, 1):
            print(f"[{i}/{len(tasks)}] {task['id']} ...", flush=True)
            # Cassette counters are per task, so a task's recorded calls do not
            # depend on how many calls the tasks before it made (issue #171).
            scope = session.scope(task["id"]) if session else nullcontext()
            try:
                with scope:
                    task_results.append(
                        _run_task(client, task, renders_dir,
                                  judge=judge and mode == MODE_PRODUCT,
                                  profile_text=profile_text,
                                  encoder=encoder, mode=mode)
                    )
            except Exception as e:
                print(f"  FAILED: {e}", file=sys.stderr)
                task_results.append({"task_id": task["id"], "error": str(e)})

        if session is not None:
            if mode == MODE_REPLAY:
                # Fatal even though every miss already raised: the degrade-
                # gracefully call sites catch everything, so a miss can reach
                # the metrics as an empty parse instead of as an error.
                session.assert_no_misses()
            if record:
                saved = session.cassette.save(cassette_path)
                print(f"Cassette → {saved} ({len(session.cassette)} interactions)")

        ok = [t for t in task_results if "error" not in t]
        results = {
            "timestamp": ts,
            "mode": mode,
            # Travels with the numbers into every consumer that reads the
            # artifact without reading this file (issue #171).
            "mode_claim": MODE_CLAIMS[mode],
            "cassette": str(cassette_path) if cassette_path else None,
            "profile": str(profile_path.relative_to(ROOT)),
            "dataset_size": len(tasks),
            "failed": [t["task_id"] for t in task_results if "error" in t],
            "aggregate": _aggregate(ok),
            "task_results": task_results,
        }

        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / f"tailoring_benchmark_{ts}.json"
        json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        _write_csv(out_dir / f"tailoring_benchmark_{ts}.csv", ok)
        print(f"\nResults → {json_path}")
        return results
    finally:
        # No-op on SQLite; drops the run's throwaway database on Postgres (#149).
        from eval.eval_db import drop_throwaway_db

        drop_throwaway_db(os.environ.get("DATABASE_URL", ""))
        if own_tmp is not None:
            try:
                own_tmp.cleanup()
            except OSError:
                pass  # Windows can hold the sqlite file briefly; temp dir, harmless


_CSV_COLUMNS = [
    ("task_id", ["task_id"]),
    # First-class column, not metadata: a CSV row loaded into pandas is where a
    # plumbing number most easily loses the context that it is one (issue #171).
    ("mode", ["mode"]),
    ("company", ["company"]),
    ("baseline_composite", ["metrics", "ats", "baseline_composite"]),
    ("tailored_composite", ["metrics", "ats", "tailored_composite"]),
    ("ats_delta", ["metrics", "ats", "delta"]),
    ("allocation_correlation", ["metrics", "experience_allocation", "allocation_correlation"]),
    ("skills_rendered", ["metrics", "skills", "rendered_count"]),
    ("skills_matched_recall", ["metrics", "skills", "matched_recall"]),
    ("selection_ratio", ["metrics", "skills", "selection_ratio"]),
    ("max_term_repetition", ["metrics", "redundancy", "max_term_repetition"]),
    ("over_repeated_count", ["metrics", "redundancy", "over_repeated_count"]),
    ("bullet_ttr", ["metrics", "redundancy", "bullet_type_token_ratio"]),
]


def _dig(d: Dict, path: List[str]):
    for key in path:
        d = d.get(key) if isinstance(d, dict) else None
    return d


def _write_csv(path: Path, task_results: List[Dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([name for name, _ in _CSV_COLUMNS])
        for t in task_results:
            writer.writerow([_dig(t, p) for _, p in _CSV_COLUMNS])


def main() -> int:
    # Windows consoles default to cp1252, which can't print the arrows/ellipses
    # in our status lines.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=MODES, default=MODE_PRODUCT,
                    help="execution mode (default: product)")
    ap.add_argument("--stub", action="store_true",
                    help="alias for --mode plumbing (kept for existing scripts)")
    ap.add_argument("--cassette", type=Path, default=None,
                    help="cassette file to record into or replay from")
    ap.add_argument("--record", action="store_true",
                    help="product mode: snapshot every model response for replay")
    ap.add_argument("--tasks", nargs="*", default=None, help="task ids to run (default: all)")
    ap.add_argument("--limit", type=int, default=0, help="run only the first N tasks")
    ap.add_argument("--profile", type=Path, default=DEFAULT_PROFILE, help="resume fixture to ingest")
    ap.add_argument("--out", type=Path, default=RESULTS_DIR, help="results directory")
    ap.add_argument("--judge", action="store_true",
                    help="add LLM-as-judge quality scores (product mode only)")
    args = ap.parse_args()

    mode = args.mode
    if args.stub:
        if args.mode != MODE_PRODUCT:
            raise SystemExit("--stub and --mode are exclusive; --stub means --mode plumbing")
        mode = MODE_PLUMBING

    results = run_benchmark(
        task_ids=args.tasks, profile_path=args.profile, mode=mode,
        limit=args.limit, out_dir=args.out, judge=args.judge,
        cassette_path=args.cassette, record=args.record,
    )
    agg = results["aggregate"]
    print(json.dumps(agg, indent=2))
    # Repeated after the numbers as well as before them: the summary is what
    # gets copied into a changelog or an issue, and it must not travel alone.
    print(_mode_banner(results["mode"]))
    return 1 if results["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
