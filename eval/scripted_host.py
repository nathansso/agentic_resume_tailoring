"""A scripted, model-free host that drives benchmark tasks through the harness (#197).

It plays the part Claude Code or Codex plays in production, with a fixed
policy instead of a model, and talks to ART only through the tool contract
(`harness.contract.invoke`) — the same calls a real host makes over MCP. Two
things are done directly because no tool covers them yet: seeding a profile
fixture into the store (ingest is the host's job, #194) and creating the job
record (`open_job` is #192).

The policy, per task:

- **Experiences:** revise each one to its three source bullets with the most
  posting overlap (in their original order, `tighten`), citing each by its
  evidence id; experiences with three or fewer bullets are kept.
- **Projects:** keep the two with the most posting overlap and delete the rest,
  as a user request.
- **Skills:** the knowledge graph's skills, posting terms first, capped.

Every choice is a pure function of the store and the posting, so two runs over
identical stores produce byte-identical results — the #197 acceptance test.

    python -m eval.scripted_host --tasks <id> [<id> ...] [--profile path.md]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional
from uuid import UUID

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.ats_scorer import ATSScoringEngine  # noqa: E402
from agents.skill_scorer import MAX_SKILLS  # noqa: E402
from harness.contract import invoke  # noqa: E402

KEEP_EXP_BULLETS = 3
KEEP_PROJECTS = 2
HOST = {"name": "scripted-host", "version": "1", "model": None}


# ── store setup (no tool for these yet) ──────────────────────────────────────

def seed_profile(fixture, name: str = "Benchmark Candidate",
                 email: str = "candidate@example.com") -> UUID:
    """Write a parsed profile fixture into the store as one user's KG."""
    from sqlmodel import Session

    import services
    from database.models import (
        Achievement, Education, Experience, Project, ProjectBlurb, Skill, User, UserSkill,
    )

    with Session(services.engine) as s:
        user = User(name=name, email=email)
        s.add(user)
        s.flush()
        uid = user.user_id
        for i, e in enumerate(fixture.experiences):
            s.add(Experience(user_id=uid, title=e["title"], company=e["company"],
                             start_date=e.get("start_date"), end_date=e.get("end_date"),
                             bullets=list(e["bullets"]), seq=i))
        for i, p in enumerate(fixture.projects):
            proj = Project(user_id=uid, name=p["name"], description=p.get("description"),
                           repo_url=p.get("repo_url"), seq=i)
            s.add(proj)
            s.flush()
            for j, b in enumerate(p["bullets"]):
                s.add(ProjectBlurb(project_id=proj.project_id, style=f"b{j:02d}", content=b))
        for i, e in enumerate(fixture.education):
            s.add(Education(user_id=uid, institution=e.get("institution") or "",
                            degree=e.get("degree"), start_date=e.get("start_date"),
                            end_date=e.get("end_date"), seq=i))
        for i, a in enumerate(fixture.achievements):
            s.add(Achievement(user_id=uid, title=a["title"], description=a.get("description"),
                              issuer=a.get("issuer"), date=a.get("date"), seq=i))
        for sk in fixture.skills:
            skill = Skill(name=sk["name"], category=sk.get("category"))
            s.add(skill)
            s.flush()
            s.add(UserSkill(user_id=uid, skill_id=skill.skill_id,
                            proficiency=sk.get("proficiency"), evidence_source="resume",
                            confidence_score=0.9))
        s.commit()
        return uid


def create_job(user_id: UUID, task: Dict) -> str:
    from sqlmodel import Session

    import services
    from database.models import JobDescription

    with Session(services.engine) as s:
        job = JobDescription(user_id=user_id, title=task["title"], company=task["company"],
                             description=task["description"], source_url=task.get("url"))
        s.add(job)
        s.commit()
        return str(job.job_id)


# ── the policy ───────────────────────────────────────────────────────────────

def _overlap(text: str, jd_terms: set) -> int:
    return len(ATSScoringEngine._extract_keywords(text or "") & jd_terms)


def _call(name: str, user_id: UUID, args: Dict) -> Dict:
    out = invoke(name, user_id, args)
    if out.get("error"):
        raise RuntimeError(f"{name}: {out['error']}")
    return out


def build_program(user_id: UUID, job_id: str, jd_text: str) -> Dict:
    jd_terms = ATSScoringEngine._extract_keywords(jd_text)
    head = _call("get_head", user_id, {"job_id": job_id})["head"]
    items = _call("list_items", user_id, {})["items"]
    nodes: List[Dict] = []

    for it in (i for i in items if i["kind"] == "experience"):
        rec = _call("get_item", user_id, {"key": it["key"]})["record"]
        bullets = rec.get("bullets") or []
        if len(bullets) <= KEEP_EXP_BULLETS:
            nodes.append({"id": f"keep:{it['key']}", "op": "keep", "item_key": it["key"]})
            continue
        ranked = sorted(range(len(bullets)), key=lambda i: (-_overlap(bullets[i], jd_terms), i))
        keep = sorted(ranked[:KEEP_EXP_BULLETS])
        nodes.append({
            "id": f"tighten:{it['key']}", "op": "revise", "item_key": it["key"],
            "strategy": "tighten",
            "bullets": [{"text": bullets[i], "cites": [f"{it['key']}#b{i}"]} for i in keep],
            "accept": {"improves": ["relevance_density"]}})

    projects = []
    for it in (i for i in items if i["kind"] == "project"):
        rec = _call("get_item", user_id, {"key": it["key"]})["record"]
        text = " ".join([rec.get("name") or "", rec.get("description") or "",
                         *(b["content"] for b in rec.get("blurbs") or [])])
        projects.append((-_overlap(text, jd_terms), it["key"]))
    for rank, (_, key) in enumerate(sorted(projects)):
        if rank >= KEEP_PROJECTS:
            nodes.append({"id": f"drop:{key}", "op": "delete", "item_key": key,
                          "because": f"user:keep the {KEEP_PROJECTS} most relevant projects"})

    skills = [i["title"] for i in items if i["kind"] == "skill"]
    skills.sort(key=lambda s: (-_overlap(s, jd_terms), s.lower()))

    return {"job_id": job_id, "parent": head["node_id"] if head else None,
            "nodes": nodes, "skills": skills[:MAX_SKILLS], "host": HOST}


def run_task(user_id: UUID, task: Dict) -> Dict:
    """Create the job, plan it, execute it. Returns `{job_id, program, result}`."""
    job_id = create_job(user_id, task)
    program = build_program(user_id, job_id, task["description"])
    result = invoke("execute_plan", user_id, {"program": program})
    return {"task": task["id"], "job_id": job_id, "program": program, "result": result}


def main(argv: Optional[List[str]] = None) -> int:
    from eval.profile_fixture import load_profile
    from eval.tailoring_benchmark import DEFAULT_PROFILE, load_tasks
    from harness.runtime import resolve_database_url

    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--tasks", nargs="*", default=None)
    p.add_argument("--limit", type=int, default=3)
    p.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    p.add_argument("--database-url",
                   help="Default: a throwaway SQLite file, so a run never touches ~/.art.")
    args = p.parse_args(argv)

    if not args.database_url:
        import tempfile
        args.database_url = f"sqlite:///{Path(tempfile.mkdtemp()) / 'scripted_host.db'}"
    os.environ["DATABASE_URL"] = resolve_database_url(args.database_url, os.environ)
    from database.db import init_db
    init_db()
    uid = seed_profile(load_profile(args.profile))
    for task in load_tasks(args.tasks, limit=0 if args.tasks else args.limit):
        out = run_task(uid, task)
        r = out["result"]
        print(json.dumps({"task": out["task"], "committed": r.get("committed"),
                          "nodes": [(n["id"], n["status"]) for n in r.get("nodes", [])],
                          "violations": r.get("violations"), "error": r.get("error")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
