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

Modes:
    python eval/tailoring_benchmark.py            # real LLM (needs API keys)
    python eval/tailoring_benchmark.py --stub     # deterministic fake LLM, offline
    python eval/tailoring_benchmark.py --tasks stripe_ai_engineer duolingo_software_engineer_i
    python eval/tailoring_benchmark.py --limit 3

The notebook eval/tailoring_benchmark.ipynb drives this module and visualizes
the artifacts.
"""
import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATASET_DIR = ROOT / "eval" / "jd_dataset"
RESULTS_DIR = ROOT / "eval" / "results"
DEFAULT_PROFILE = ROOT / "eval" / "profiles" / "benchmark_profile.md"

BENCH_EMAIL = "benchmark@example.com"
BENCH_PASSWORD = "benchmark-pass-123"


# ── environment isolation (must run before any project import) ────────────────

def _prepare_environment(workdir: Path) -> None:
    """
    Point every stateful surface at the temp workdir and force the offline
    local-cookie auth mode. config.py reads these at import time, so this must
    run before web.app / database.db are imported.
    """
    os.environ["DATABASE_URL"] = f"sqlite:///{workdir / 'benchmark.db'}"
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

# Canned parse of eval/profiles/benchmark_profile.md — what the parser stub
# "extracts". Kept aligned with the fixture so rendered resumes read sensibly.
STUB_EXPERIENCES = [
    {"title": "Machine Learning Engineer", "company": "Nimbus Analytics",
     "start_date": "2024-01", "end_date": "Present",
     "description": "ML systems and ranking models",
     "bullets": [
         "Built and deployed gradient-boosted and transformer ranking models serving 2M daily predictions with PyTorch and XGBoost.",
         "Designed a feature store on Postgres and Redis, cutting feature backfill time from hours to minutes.",
         "Set up model monitoring with drift detection, alerting, and automated retraining on Airflow.",
         "Fine-tuned sentence-transformer embedding models for semantic product search, lifting recall@10 by 18%.",
     ]},
    {"title": "Backend Software Engineer", "company": "Bluefin Software",
     "start_date": "2022-03", "end_date": "2024-01",
     "description": "Backend microservices",
     "bullets": [
         "Developed FastAPI and Flask microservices handling 40k requests/minute behind an Nginx gateway.",
         "Modeled billing and subscription data in Postgres with SQLAlchemy; wrote migrations with Alembic.",
         "Containerized services with Docker and deployed to AWS ECS through GitHub Actions CI/CD.",
         "Led the migration from a monolith to event-driven services using Kafka.",
     ]},
    {"title": "Software Engineer", "company": "Harbor Labs",
     "start_date": "2021-06", "end_date": "2022-03",
     "description": "Dashboards and ETL",
     "bullets": [
         "Built React and TypeScript dashboards visualizing pipeline health for internal teams.",
         "Wrote Python ETL jobs with pandas processing 50GB of daily event data into Snowflake.",
         "Added integration tests with pytest and cut flaky-test rate by half.",
     ]},
    {"title": "Student Developer", "company": "City University IT Department",
     "start_date": "2020-09", "end_date": "2021-06",
     "description": "Internal tooling",
     "bullets": [
         "Maintained PHP and MySQL tooling for course registration workflows.",
         "Automated report generation with Python scripts, saving staff ten hours weekly.",
     ]},
]

STUB_PROJECTS = [
    {"name": "SemanticSearch-Lite", "description": "Semantic search library",
     "repo_url": "https://github.com/alexrivera/semsearch",
     "bullets": [
         "Open-source semantic search library using sentence-transformers, FAISS, and a FastAPI serving layer; 400+ GitHub stars.",
         "Implemented hybrid BM25 + dense retrieval with reciprocal rank fusion.",
     ]},
    {"name": "StreamBoard", "description": "Real-time analytics dashboard",
     "bullets": ["Real-time analytics dashboard with Kafka, ClickHouse, and a React frontend; processes 10k events/second."]},
    {"name": "LLM Resume Coach", "description": "Resume critique app",
     "bullets": ["LangChain + OpenAI application that critiques resumes against job descriptions; deployed on Fly.io with Docker."]},
    {"name": "Pixel Adventure", "description": "2D platformer game",
     "bullets": ["2D platformer game in C# and Unity published on itch.io."]},
]

STUB_SKILLS = [
    {"name": n, "category": c, "proficiency": p}
    for n, c, p in [
        ("Python", "Language", 5), ("TypeScript", "Language", 4), ("C#", "Language", 3),
        ("SQL", "Language", 4), ("PyTorch", "Library", 4), ("XGBoost", "Library", 4),
        ("scikit-learn", "Library", 4), ("sentence-transformers", "Library", 4),
        ("LangChain", "Framework", 3), ("pandas", "Library", 4), ("NumPy", "Library", 4),
        ("Airflow", "Tool", 3), ("Kafka", "Tool", 4), ("ClickHouse", "Database", 3),
        ("Snowflake", "Database", 3), ("FAISS", "Library", 3), ("FastAPI", "Framework", 5),
        ("Flask", "Framework", 4), ("React", "Framework", 4), ("Node.js", "Framework", 3),
        ("Postgres", "Database", 4), ("MySQL", "Database", 3), ("Redis", "Database", 3),
        ("Docker", "Tool", 4), ("Kubernetes", "Tool", 3), ("AWS", "Cloud", 4),
        ("GitHub Actions", "Tool", 4), ("Nginx", "Tool", 3), ("Unity", "Tool", 2),
        ("Git", "Tool", 5),
    ]
]

# JD-skill vocabulary the stub "analyzer" scans job text against: the profile's
# skills plus common terms the profile lacks, so missing_skills is non-empty.
STUB_JD_VOCAB = [s["name"] for s in STUB_SKILLS] + [
    "Java", "Go", "Rust", "Scala", "GraphQL", "Spark", "TensorFlow", "Terraform",
    "GCP", "Azure", "MongoDB", "Elasticsearch", "machine learning", "deep learning",
    "LLM", "microservices", "REST", "CI/CD",
]


def _stub_extract_jd_skills(jd_text: str) -> List[Dict]:
    low = jd_text.lower()
    out = []
    for name in STUB_JD_VOCAB:
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


def _stub_tailored(jd_text: str) -> Dict:
    low = jd_text.lower()
    emphasized = [s["name"] for s in STUB_SKILLS if s["name"].lower() in low]
    return {
        "experiences": [
            {k: e[k] for k in ("title", "company", "start_date", "end_date", "bullets")}
            for e in STUB_EXPERIENCES
        ],
        "projects": [
            {"name": p["name"], "selected_style": "technical", "bullets": p["bullets"]}
            for p in STUB_PROJECTS
        ],
        "skills_emphasized": emphasized or [s["name"] for s in STUB_SKILLS[:8]],
    }


def _stub_payload(text: str):
    """Route a formatted prompt to its canned/deterministic payload (dict/list)."""
    if "Extract work experiences" in text:
        return STUB_EXPERIENCES
    if "Extract projects" in text:
        return [{k: p[k] for k in ("name", "description")} | (
            {"repo_url": p["repo_url"]} if "repo_url" in p else {}) for p in STUB_PROJECTS]
    if "Extract technical skills" in text:
        return STUB_SKILLS
    if "Extract the job title" in text:
        return {"title": "Benchmark Role", "company": "Benchmark Co"}
    if "job description analyzer" in text:
        return _stub_extract_jd_skills(text)
    if "resume tailoring assistant" in text:
        return _stub_tailored(text)
    return {}


def _prompt_text(prompt_value) -> str:
    """Text of a PromptValue or a list of formatted messages."""
    if hasattr(prompt_value, "to_string"):
        return prompt_value.to_string()
    if isinstance(prompt_value, (list, tuple)):
        return "\n".join(str(getattr(m, "content", m)) for m in prompt_value)
    return str(prompt_value)


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
    """Deterministic hash-based embedder — no model download, stable vectors."""

    def encode(self, texts, normalize_embeddings=True, **kwargs):
        import numpy as np
        single = isinstance(texts, str)
        items = [texts] if single else list(texts)
        vecs = []
        for t in items:
            rng = np.random.default_rng(abs(hash(t.lower())) % (2**32))
            v = rng.standard_normal(32)
            v /= np.linalg.norm(v)
            vecs.append(v)
        arr = np.asarray(vecs)
        return arr[0] if single else arr


def _install_stubs() -> None:
    """Patch the LLM factory and the embedding model everywhere they were bound."""
    import agents.job_analyzer as job_analyzer
    import agents.matcher as matcher
    import agents.parser as parser
    import agents.tailor as tailor

    stub = _make_stub_llm()
    for module in (parser, job_analyzer, tailor):
        module.get_llm = stub
    matcher.get_embedding_model = lambda: _StubEmbeddingModel()
    matcher._embedding_model = None


# ── dataset ────────────────────────────────────────────────────────────────────

def load_tasks(task_ids: Optional[List[str]] = None, limit: int = 0) -> List[Dict]:
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
    return tasks[:limit] if limit else tasks


# ── benchmark run ──────────────────────────────────────────────────────────────

def _api(client, method: str, url: str, **kwargs):
    resp = getattr(client, method)(url, **kwargs)
    if resp.status_code >= 400:
        raise RuntimeError(f"{method.upper()} {url} → {resp.status_code}: {resp.text[:500]}")
    return resp


def _run_task(client, task: Dict, renders_dir: Path,
              judge: bool = False, profile_text: str = "") -> Dict:
    """Drive one JD through the exact user flow and compute its metrics."""
    from sqlmodel import Session, select

    from database.db import engine
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
        result = max(results, key=lambda r: r.created_at)
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
        "company": task["company"],
        "title": task["title"],
        "job_id": job_id,
        "ats_score": detail.get("ats_score"),
        "metrics": metrics,
    }


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
        "judge_mean_score": stats(collect(["llm_judge", "mean_score"])),
    }


def run_benchmark(
    task_ids: Optional[List[str]] = None,
    profile_path: Path = DEFAULT_PROFILE,
    stub: bool = False,
    limit: int = 0,
    out_dir: Path = RESULTS_DIR,
    workdir: Optional[Path] = None,
    judge: bool = False,
) -> Dict:
    """Full benchmark run. Returns the results dict (also persisted to out_dir)."""
    tasks = load_tasks(task_ids, limit)
    if not tasks:
        raise SystemExit(f"No tasks found in {DATASET_DIR} — run scripts/scrape_job_descriptions.py")

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
        if stub:
            _install_stubs()
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
        task_results = []
        for i, task in enumerate(tasks, 1):
            print(f"[{i}/{len(tasks)}] {task['id']} ...", flush=True)
            try:
                task_results.append(
                    _run_task(client, task, renders_dir,
                              judge=judge and not stub, profile_text=profile_text)
                )
            except Exception as e:
                print(f"  FAILED: {e}", file=sys.stderr)
                task_results.append({"task_id": task["id"], "error": str(e)})

        ok = [t for t in task_results if "error" not in t]
        results = {
            "timestamp": ts,
            "mode": "stub" if stub else "llm",
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
        if own_tmp is not None:
            try:
                own_tmp.cleanup()
            except OSError:
                pass  # Windows can hold the sqlite file briefly; temp dir, harmless


_CSV_COLUMNS = [
    ("task_id", ["task_id"]),
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
    ap.add_argument("--stub", action="store_true", help="deterministic fake LLM (offline)")
    ap.add_argument("--tasks", nargs="*", default=None, help="task ids to run (default: all)")
    ap.add_argument("--limit", type=int, default=0, help="run only the first N tasks")
    ap.add_argument("--profile", type=Path, default=DEFAULT_PROFILE, help="resume fixture to ingest")
    ap.add_argument("--out", type=Path, default=RESULTS_DIR, help="results directory")
    ap.add_argument("--judge", action="store_true",
                    help="add LLM-as-judge quality scores (real-LLM mode only)")
    args = ap.parse_args()

    results = run_benchmark(
        task_ids=args.tasks, profile_path=args.profile, stub=args.stub,
        limit=args.limit, out_dir=args.out, judge=args.judge,
    )
    agg = results["aggregate"]
    print(json.dumps(agg, indent=2))
    return 1 if results["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
