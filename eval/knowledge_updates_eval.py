"""Knowledge-Updates regression eval (issue #21, on the #51 harness).

The LongMemEval-derived regression the Phase 1 epic (#140) calls for: when a
later chat turn **contradicts an earlier fact**, does the knowledge graph
*update*, or does it go stale (or, worse, sprout a duplicate row)?

Each task in `eval/ku_dataset/` is a small closed world: the rows already in the
graph (`seed`), a short transcript whose last turn revises one of them
(`turns`), and the post-state the graph must be in (`expect`). The eval seeds a
throwaway profile, runs the Chain-of-Note pipeline
(`agents/knowledge_extractor.run_chain_of_note`), applies every proposal through
`services.apply_artifact_decision` — the same explicit-accept path the chat
panel uses — and then reads the rows back.

Default mode is offline and deterministic: the task file supplies the notes and
the decisions, so what is under test is the *pipeline contract* — the
deterministic `decide` layer and the supersede persistence — not a model's mood
on the day. That is what makes it a regression eval rather than a benchmark.
`--live` swaps in real extractors through the #142 seam to measure the model
itself.

    python eval/knowledge_updates_eval.py           # offline, deterministic
    python eval/knowledge_updates_eval.py --live    # real LLM (needs API keys)
    python eval/knowledge_updates_eval.py --tasks promotion_supersedes_experience

Metric: `update_accuracy` — the fraction of tasks whose post-state matches
`expect` exactly. A stale graph and a duplicated row both score 0.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATASET_DIR = ROOT / "eval" / "ku_dataset"
RESULTS_DIR = ROOT / "eval" / "results"


# ── dataset ───────────────────────────────────────────────────────────────────

def load_tasks(task_ids: Optional[List[str]] = None) -> List[Dict]:
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
    return tasks


# ── scripted extractors (offline mode) ────────────────────────────────────────

class ScriptedExtractor:
    """A `StructuredExtractor` stand-in returning a fixed validated model.

    Same shape as the seam's real return value, so the pipeline under test runs
    completely unmodified — no branch in production code knows this exists.
    """

    def __init__(self, payload):
        self._payload = payload

    def invoke(self, _messages):
        return self._payload


def scripted_extractors(task: Dict):
    """(extract_extractor, reason_extractor) canned from the task file."""
    from agents.extraction_schemas import ArtifactDecisionList, ChatArtifactNoteList

    notes = ChatArtifactNoteList(notes=task.get("notes") or [])
    decisions = ArtifactDecisionList(decisions=task.get("decisions") or [])
    return ScriptedExtractor(notes), ScriptedExtractor(decisions)


# ── seeding + readback ────────────────────────────────────────────────────────

def seed_profile(engine, task: Dict):
    """Create a fresh user and the graph rows the task starts from."""
    from sqlmodel import Session, select

    from database.models import Experience, Project, Skill, User, UserSkill

    seed = task.get("seed") or {}
    with Session(engine) as session:
        user = User(name="KU Eval User", email=f"ku-{task['id']}@example.com")
        session.add(user)
        session.commit()
        session.refresh(user)
        uid = user.user_id

        for item in seed.get("skills") or []:
            skill = session.exec(
                select(Skill).where(Skill.name == item["name"])).first()
            if not skill:
                skill = Skill(name=item["name"], category=item.get("category"))
                session.add(skill)
                session.flush()
            session.add(UserSkill(
                user_id=uid, skill_id=skill.skill_id,
                proficiency=item.get("proficiency"),
                evidence_source="resume", confidence_score=0.9,
            ))
        for item in seed.get("experiences") or []:
            session.add(Experience(
                user_id=uid, title=item["title"], company=item["company"],
                description=item.get("description"),
            ))
        for item in seed.get("projects") or []:
            session.add(Project(
                user_id=uid, name=item["name"], description=item.get("description"),
                repo_url=item.get("repo_url"),
            ))
        session.commit()
    return uid


def read_graph(engine, user_id) -> Dict[str, List[Dict]]:
    """The user's post-run graph rows, flattened for assertion."""
    from sqlmodel import Session, select

    from database.models import Experience, Project, Skill, UserSkill

    with Session(engine) as session:
        skills = []
        for link in session.exec(
            select(UserSkill).where(UserSkill.user_id == user_id)
        ).all():
            skill = session.get(Skill, link.skill_id)
            if skill:
                skills.append({
                    "name": skill.name, "category": skill.category,
                    "proficiency": link.proficiency,
                    "source_context": link.source_context,
                })
        experiences = [
            {"title": e.title, "company": e.company, "description": e.description,
             "source_context": e.source_context}
            for e in session.exec(
                select(Experience).where(Experience.user_id == user_id)).all()
        ]
        projects = [
            {"name": p.name, "description": p.description,
             "source_context": p.source_context}
            for p in session.exec(
                select(Project).where(Project.user_id == user_id)).all()
        ]
    return {"skills": skills, "experiences": experiences, "projects": projects}


# ── assertions ────────────────────────────────────────────────────────────────

def _norm(value) -> str:
    return str(value or "").strip().lower()


def check_expectations(expect: Dict, proposals: List[Dict],
                       graph: Dict[str, List[Dict]]) -> List[str]:
    """Return a list of failure strings; empty means the task passed."""
    failures: List[str] = []

    wanted = expect.get("decisions")
    if wanted is not None:
        got = sorted(p.get("decision") or "" for p in proposals)
        if got != sorted(wanted):
            failures.append(f"decisions: expected {sorted(wanted)}, got {got}")

    for kind, key in (("skills", "name"), ("experiences", "title"),
                      ("projects", "name")):
        for want in expect.get(kind) or []:
            rows = graph[kind]
            if kind == "experiences":
                matches = [r for r in rows
                           if _norm(r["company"]) == _norm(want["company"])]
                label = f"experience @ {want['company']}"
            else:
                matches = [r for r in rows if _norm(r[key]) == _norm(want[key])]
                label = f"{kind[:-1]} '{want[key]}'"

            expected_count = want.get("count", 1)
            if len(matches) != expected_count:
                # The two failure modes this eval exists to catch: 0 = the update
                # was dropped, >1 = it duplicated instead of superseding.
                failures.append(
                    f"{label}: expected {expected_count} row(s), got {len(matches)}")
                continue
            for field, value in want.items():
                if field in ("count", "company"):
                    continue
                actual = matches[0].get(field)
                if field.endswith("_contains"):
                    base = field[: -len("_contains")]
                    if _norm(value) not in _norm(matches[0].get(base)):
                        failures.append(
                            f"{label}: {base} {matches[0].get(base)!r} "
                            f"does not contain {value!r}")
                elif _norm(actual) != _norm(value):
                    failures.append(
                        f"{label}: {field} expected {value!r}, got {actual!r}")

    for kind in ("skills", "experiences", "projects"):
        total = expect.get(f"{kind}_total")
        if total is not None and len(graph[kind]) != total:
            failures.append(
                f"{kind}: expected {total} row(s) in total, got {len(graph[kind])}")
    return failures


# ── runner ────────────────────────────────────────────────────────────────────

def _isolated_engine(workdir: Path):
    """A temp SQLite engine bound into every module that holds an engine ref."""
    from sqlmodel import SQLModel, create_engine

    import database.db as db_module
    import knowledge_graph.builder as kg_module
    import services as services_module

    engine = create_engine(
        f"sqlite:///{workdir / 'ku_eval.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    db_module.engine = engine
    kg_module.engine = engine
    services_module.engine = engine
    return engine


def run_task(task: Dict, engine, live: bool = False) -> Dict:
    """Seed → extract/reason/decide → apply → read back. Returns the task record."""
    import services
    from agents.knowledge_extractor import load_known_facts, run_chain_of_note

    user_id = seed_profile(engine, task)
    known_facts = load_known_facts(user_id)

    extract_extractor = reason_extractor = None
    if not live:
        extract_extractor, reason_extractor = scripted_extractors(task)

    proposals = run_chain_of_note(
        task.get("turns") or [], known_facts,
        extract_extractor=extract_extractor, reason_extractor=reason_extractor,
    )

    # Every proposal is applied, exactly as if the user had accepted it — the
    # confirmation gate lives in the chat/API layer, not here.
    applied = [
        services.apply_artifact_decision(user_id, p, source_context=f"ku:{task['id']}")
        for p in proposals
    ]

    graph = read_graph(engine, user_id)
    failures = check_expectations(task.get("expect") or {}, proposals, graph)
    return {
        "task_id": task["id"],
        "passed": not failures,
        "failures": failures,
        "decisions": [p.get("decision") for p in proposals],
        "messages": applied,
    }


def run_eval(
    task_ids: Optional[List[str]] = None,
    engine=None,
    live: bool = False,
    out_dir: Optional[Path] = RESULTS_DIR,
    workdir: Optional[Path] = None,
) -> Dict:
    """Run the dataset. Pass `engine` to reuse a caller-owned isolated engine."""
    import tempfile

    tasks = load_tasks(task_ids)
    if not tasks:
        raise SystemExit(f"No tasks found in {DATASET_DIR}")

    own_tmp = None
    if engine is None:
        if workdir is None:
            own_tmp = tempfile.TemporaryDirectory(prefix="art_ku_eval_")
            workdir = Path(own_tmp.name)
        engine = _isolated_engine(Path(workdir))

    try:
        records = [run_task(task, engine, live=live) for task in tasks]
        passed = sum(1 for r in records if r["passed"])
        results = {
            "timestamp": datetime.now().strftime("%Y%m%dT%H%M%S"),
            "mode": "live" if live else "scripted",
            "tasks": len(records),
            "update_accuracy": round(passed / len(records), 3),
            "task_results": records,
        }
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"knowledge_updates_{results['timestamp']}.json"
            path.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                            encoding="utf-8")
            print(f"\nResults → {path}")
        return results
    finally:
        if own_tmp is not None:
            try:
                own_tmp.cleanup()
            except OSError:
                pass  # Windows can hold the sqlite file briefly; temp dir, harmless


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true",
                    help="use real extractors instead of the scripted ones")
    ap.add_argument("--tasks", nargs="*", default=None, help="task ids to run")
    ap.add_argument("--out", type=Path, default=RESULTS_DIR, help="results directory")
    args = ap.parse_args()

    results = run_eval(task_ids=args.tasks, live=args.live, out_dir=args.out)
    for record in results["task_results"]:
        mark = "PASS" if record["passed"] else "FAIL"
        print(f"[{mark}] {record['task_id']}")
        for failure in record["failures"]:
            print(f"       {failure}")
    print(f"\nupdate_accuracy: {results['update_accuracy']} "
          f"({results['tasks']} task(s), mode={results['mode']})")
    return 0 if results["update_accuracy"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
