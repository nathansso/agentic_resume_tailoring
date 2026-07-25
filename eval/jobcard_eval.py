"""JobCard card-quality eval (issue #137, on the #51 harness).

Asks the question the JobCard design has to answer: **is a past job summarized
well enough?** The card claims to be a *sufficient statistic* for the next
decision — everything that mattered about a finished job, in a fraction of the
tokens. That claim is measurable, so it is measured rather than assumed.

Three nested signals, as #137 specifies:

1. **Functional equivalence** — `sim(plan | card, plan | full_history)`. Two
   arms plan the same next job: one reading the compiled card, one reading the
   raw finished result. If the card dropped something decision-relevant, the
   plans diverge. Compared as *typed plans* — op per item — not free text, per
   the issue's own note: the action is a structured op x strategy plan, so a
   structural diff is both cheaper and far less noisy than trajectory
   similarity.

2. **Downstream outcome** — the card-informed plan is executed into a resume and
   compared with a memoryless plan on two figures. `ats_composite_delta` uses
   the real `ATSScoringEngine`; `relevance_delta` uses JD-keyword density. Both,
   because ATS is only one term of the intended objective — #127 defines it as
   `net(a) = ΔATS − λ·Δcost` — and the composite is provably unable to reward a
   deletion (see `_relevance_density`). Reporting the composite alone would say
   the memory did nothing.

3. **Negation guard** — a card that loses a `user-rejected` item which then
   *recurs* in the next job is penalized `NEGATION_WEIGHT` times harder than a
   generic-recap miss. #129 finding 1 is the reason: opposed-case retrieval is
   where summarization silently fails (F1 14.8%), so an unweighted average would
   score a card that forgets a rejection almost as well as a perfect one.

    card_quality = 1 - Σ(w · miss) / Σ(w)

Default mode is offline and deterministic. The planner arm is a decision rule
over a structured memory record, so what is under test is **information
preservation by the compile** — not a model's mood on the day. That is what
makes this a regression eval rather than a benchmark, the same choice
`eval/knowledge_updates_eval.py` made for #21. `--live` swaps in the real
`TailorPlanner` at temperature 0 to measure the model instead.

    python eval/jobcard_eval.py                 # offline, deterministic
    python eval/jobcard_eval.py --live          # real planner (needs API keys)
    python eval/jobcard_eval.py --tasks rejected_project_recurs
    python eval/jobcard_eval.py --no-ablation   # skip the field-ablation sweep

Metric: `card_quality` (weighted, see above) plus `functional_equivalence`,
`ats_composite_delta`, and a per-field ablation table showing which card fields carry
the signal.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATASET_DIR = ROOT / "eval" / "jobcard_dataset"
RESULTS_DIR = ROOT / "eval" / "results"

# How much harder a lost rejection weighs than a generic miss. Deliberately
# well above 1: the whole reason this tier carries rejections is that nothing
# else does, so losing one is a qualitatively different failure.
NEGATION_WEIGHT = 3.0

# Card fields swept by the ablation. Each is blanked in turn and the eval is
# re-run, so the report shows which fields actually carry decision-relevant
# information rather than asserting it.
#
# Read the table with the probe rule in mind: it consumes `emphasized` and
# `rejected_items`, so `user_score` and `ats` ablate to +0.000 here. That is a
# statement about *this* metric, not a verdict that the fields are dead weight —
# they are carried for the planner prompt and for #51 Phase 2 / #119 to consume
# downstream. A live (`--live`) run, where the real planner sees the whole
# rendered card, is what would put a number on them.
ABLATION_FIELDS = ("rejected_items", "emphasized", "user_score", "ats")


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


def _rows(prior: Dict):
    """The task's prior job as the duck-typed (job, result) pair the compile
    takes — the same shape the ORM hands it in production."""
    job = SimpleNamespace(
        title=prior.get("title", ""), company=prior.get("company", ""),
        description=prior.get("description", ""), status=prior.get("status", "tailored"),
    )
    result = SimpleNamespace(
        ats_score=prior.get("ats_score", 0.0),
        tailored_score_breakdown=prior.get("tailored_score_breakdown", {}),
        score_breakdown=prior.get("score_breakdown", {}),
        matched_skills=prior.get("matched_skills", {}),
        tailored_resume_content=prior.get("tailored_resume_content", {}),
        tailoring_decisions=prior.get("tailoring_decisions", []),
        verification_status=prior.get("verification_status", "pending"),
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 6, 1),
    )
    return job, result


# ── the two memory arms ───────────────────────────────────────────────────────
#
# Both produce the same structured record, so the planner rule below is a pure
# function of it and the comparison isolates exactly one variable: whether the
# card preserved what the raw result held.

def _norm(value) -> str:
    from agents.job_card import _norm as normalize
    return normalize(value)


def memory_from_full_history(prior: Dict) -> Dict:
    """Ground truth: read the finished result directly, undistilled.

    This is the arm the card has to match. It walks the whole decision log and
    the whole tailored content — everything a "stuff the prior transcripts into
    the prompt" approach would have had access to.
    """
    content = prior.get("tailored_resume_content") or {}
    emphasized = [
        _norm(e.get("title")) for e in content.get("experiences") or []
    ] + [_norm(p.get("name")) for p in content.get("projects") or []]

    removed, score = [], None
    latest: Dict[str, Dict] = {}
    for entry in prior.get("tailoring_decisions") or []:
        user_driven = (
            entry.get("planner") == "chat_approved"
            or bool((entry.get("revision_notes") or "").strip())
        )
        for action in entry.get("actions") or []:
            latest[action.get("item_key", "")] = {
                "op": action.get("op"), "user": user_driven,
                "label": action.get("label"),
            }
        if (entry.get("reward") or {}).get("user_score") is not None:
            score = entry["reward"]["user_score"]
    for key, action in latest.items():
        if action["op"] in ("delete", "replace") and action["user"]:
            removed.append(_norm(action["label"] or key))

    return {
        "emphasized": [e for e in emphasized if e],
        "removed": sorted(set(removed)),
        "user_score": score,
    }


def memory_from_card(payload: Dict) -> Dict:
    """The same record, read off the compiled card.

    Reads only *active* rejections. The card also retains reversed ones so the
    trajectory is recoverable for policy training (#133: negation must not
    expire), but a reversed rejection is history, not a standing preference —
    counting it here would suppress an item the user deliberately restored.
    """
    from agents.job_card import active_rejections

    emphasized = payload.get("emphasized") or {}
    labels = [
        _norm(label.split(" @ ")[0]) for label in emphasized.get("experiences") or []
    ] + [_norm(label) for label in emphasized.get("projects") or []]
    removed = [
        _norm(r.get("label") or r.get("item_key"))
        for r in active_rejections(payload, source="user")
    ]
    return {
        "emphasized": [e for e in labels if e],
        "removed": sorted(set(r for r in removed if r)),
        "user_score": payload.get("user_score"),
    }


def ablate(payload: Dict, field: str) -> Dict:
    """A copy of the card with one field blanked — the ablation arm."""
    blanks = {
        "rejected_items": [],
        "emphasized": {"experiences": [], "projects": [], "skills": [],
                       "led_experience": None, "led_project": None},
        "user_score": None,
        "ats": {"composite": None, "components": {}},
    }
    return {**payload, field: blanks[field]}


# ── the planner arms ──────────────────────────────────────────────────────────

def plan_from_memory(items: List[Dict], memory: Dict) -> Dict[str, str]:
    """Deterministic decision rule over a memory record → {item_key: op}.

    Not a stand-in for the production planner — a *probe*. Its only job is to be
    a function that genuinely depends on the memory, so that plan divergence
    between the two arms means information loss and nothing else. A rule that
    ignored the memory would score 1.0 for any card, including an empty one.
    """
    plan = {}
    for item in items:
        label = _norm(item.get("label") or item.get("key"))
        if any(label and label == r for r in memory.get("removed") or []):
            plan[item["key"]] = "delete"
        elif any(label and label == e for e in memory.get("emphasized") or []):
            plan[item["key"]] = "keep"
        else:
            plan[item["key"]] = "revise"
    return plan


def plan_from_planner(items: List[Dict], cards: List[Dict], jd_text: str) -> Dict[str, str]:
    """`--live` arm: the real planner, temperature 0.

    The issue's own note pins this: the planner is stochastic, so equivalence
    must be measured at temperature 0 or averaged over samples, otherwise the
    metric reports LLM sampling variance and mislabels it as summarization loss.
    """
    from llm import get_llm
    from agents.tailor_planner import TailorPlanner

    planner = TailorPlanner(llm=get_llm(role="tailor", temperature=0.0))
    plan = planner.plan(items=items, pool=[], jd_text=jd_text, missing_skills=[],
                        job_cards=cards)
    return {a["item_key"]: a["op"] for a in plan.get("actions") or []}


# ── scoring ───────────────────────────────────────────────────────────────────

def _execute_plan(items: List[Dict], plan: Dict[str, str]) -> Dict:
    """Turn a plan into resume content so the real ATS engine can score it."""
    experiences, projects = [], []
    for item in items:
        if plan.get(item["key"]) == "delete":
            continue
        row = {"bullets": [item.get("source_text", "")]}
        if item.get("section") == "experience":
            experiences.append({**row, "title": item.get("label"), "company": ""})
        else:
            projects.append({**row, "name": item.get("label")})
    return {"experiences": experiences, "projects": projects,
            "skills_emphasized": []}


def _composite(items: List[Dict], plan: Dict[str, str], task: Dict) -> float:
    from agents.ats_scorer import ATSScoringEngine

    next_job = task["next_job"]
    breakdown = ATSScoringEngine.score_tailored(
        _execute_plan(items, plan), next_job.get("description", ""),
        matched_skills=next_job.get("matched_skills") or {},
    )
    return round(float(breakdown.get("composite") or 0.0), 3)


def _relevance_density(items: List[Dict], plan: Dict[str, str], task: Dict) -> float:
    """Fraction of the resume's content tokens that are JD keywords.

    Reported alongside the ATS composite because the composite alone **cannot
    see this issue's main benefit**, and that is a known property of the scorer
    rather than a discovery here: **#127** records that 0.75 of the composite
    (`skill_coverage` 0.45 + `keyword_coverage` 0.30) is coverage, coverage is
    monotone non-decreasing in edits, and marginal ATS therefore "saturates
    toward zero but essentially never goes negative". A deletion can only hold
    or lower it, so the composite can never reward honouring a rejection.

    What a good deletion changes is *precision*: the same signal in fewer words.
    That is the cost side of #127's `net(a) = ΔATS − λ·Δcost` objective, whose
    proper cost term is the #122 redundancy suite (semantic duplication,
    stuffing, dilution). This density figure is a stand-in for that term until
    #122 lands — reusing `eval/metrics._keyword_relevance`, already on the #51
    harness — and it is why both numbers are reported rather than one.
    """
    from agents.ats_scorer import ATSScoringEngine
    from eval.metrics import _keyword_relevance

    text = ATSScoringEngine.flatten_tailored_text(_execute_plan(items, plan))
    jd_keywords = ATSScoringEngine._extract_keywords(
        task["next_job"].get("description", ""))
    return round(_keyword_relevance(text, jd_keywords), 4)


def score_plans(
    items: List[Dict], reference: Dict[str, str], candidate: Dict[str, str],
    recurring_rejected: List[str],
) -> Dict:
    """Weighted agreement between the card arm and the full-history arm.

    Every item contributes 1.0, except items the prior job's user explicitly
    rejected *and which recur here* — those contribute NEGATION_WEIGHT, because
    forgetting one is the failure mode this whole tier exists to prevent.
    """
    recurring = {str(k) for k in recurring_rejected or []}
    weighted_total = weighted_miss = 0.0
    matches = 0
    negation_failures: List[str] = []

    for item in items:
        key = item["key"]
        weight = NEGATION_WEIGHT if key in recurring else 1.0
        weighted_total += weight
        if candidate.get(key) == reference.get(key):
            matches += 1
            continue
        weighted_miss += weight
        if key in recurring:
            negation_failures.append(
                f"{key}: the card lost a user rejection that recurs here "
                f"(expected {reference.get(key)!r}, got {candidate.get(key)!r})")

    return {
        "functional_equivalence": round(matches / len(items), 3) if items else 1.0,
        "card_quality": round(1.0 - weighted_miss / weighted_total, 3)
        if weighted_total else 1.0,
        "negation_failures": negation_failures,
    }


# ── runner ────────────────────────────────────────────────────────────────────

def run_task(task: Dict, live: bool = False, ablation: bool = True) -> Dict:
    """Compile the card, plan both arms, score, then sweep the ablations."""
    from agents.job_card import build_index_keys, compile_card_payload

    prior, next_job = task["prior_job"], task["next_job"]
    items = next_job["items"]
    job, result = _rows(prior)

    # role_family is supplied by the task file: it is the one part of the
    # compile that is a model call, and pinning it keeps the default mode
    # offline and the compile's determinism the only thing under test.
    payload = compile_card_payload(job, result, role_family=prior.get("role_family"))
    card = {
        "card_id": task["id"], "payload": payload,
        "index_keys": build_index_keys(payload),
        "role_family": prior.get("role_family"),
    }

    reference = plan_from_memory(items, memory_from_full_history(prior))
    if live:
        candidate = plan_from_planner(items, [card], next_job.get("description", ""))
    else:
        candidate = plan_from_memory(items, memory_from_card(payload))

    recurring = task.get("recurring_rejected") or []
    scores = score_plans(items, reference, candidate, recurring)

    # Downstream outcome: does the card-informed plan beat a memoryless one?
    # Both arms of the answer — see _relevance_density for why the composite
    # alone under-reports.
    memoryless = plan_from_memory(items, {})
    scores["ats_composite_delta"] = round(
        _composite(items, candidate, task) - _composite(items, memoryless, task), 3)
    scores["relevance_delta"] = round(
        _relevance_density(items, candidate, task)
        - _relevance_density(items, memoryless, task), 4)

    ablations: Dict[str, Dict] = {}
    if ablation and not live:
        for field in ABLATION_FIELDS:
            reduced = plan_from_memory(items, memory_from_card(ablate(payload, field)))
            reduced_scores = score_plans(items, reference, reduced, recurring)
            ablations[field] = {
                "card_quality": reduced_scores["card_quality"],
                "delta": round(
                    reduced_scores["card_quality"] - scores["card_quality"], 3),
                "negation_failures": len(reduced_scores["negation_failures"]),
            }

    # The negation weighting is a *per-task* property: a field that is
    # catastrophic on one task and irrelevant on three looks mild in the mean.
    # Recording the worst case keeps that visible in the aggregate below.

    minimum = (task.get("expect") or {}).get("min_card_quality", 1.0)
    return {
        "task_id": task["id"],
        "passed": scores["card_quality"] >= minimum,
        "min_card_quality": minimum,
        **scores,
        "ablations": ablations,
    }


def run_eval(
    task_ids: Optional[List[str]] = None,
    live: bool = False,
    ablation: bool = True,
    out_dir: Optional[Path] = RESULTS_DIR,
) -> Dict:
    tasks = load_tasks(task_ids)
    if not tasks:
        raise SystemExit(f"No tasks found in {DATASET_DIR}")

    records = [run_task(task, live=live, ablation=ablation) for task in tasks]
    quality = [r["card_quality"] for r in records]
    equivalence = [r["functional_equivalence"] for r in records]
    deltas = [r["ats_composite_delta"] for r in records]
    relevance = [r["relevance_delta"] for r in records]

    aggregate_ablation: Dict[str, Dict] = {}
    for field in ABLATION_FIELDS:
        rows = [r["ablations"][field] for r in records if field in r["ablations"]]
        if rows:
            aggregate_ablation[field] = {
                "mean_card_quality": round(
                    sum(r["card_quality"] for r in rows) / len(rows), 3),
                "mean_delta": round(sum(r["delta"] for r in rows) / len(rows), 3),
                "worst_card_quality": min(r["card_quality"] for r in rows),
                "worst_delta": min(r["delta"] for r in rows),
                "negation_failures": sum(r["negation_failures"] for r in rows),
            }

    results = {
        "timestamp": datetime.now().strftime("%Y%m%dT%H%M%S"),
        "mode": "live" if live else "scripted",
        "tasks": len(records),
        "card_quality": round(sum(quality) / len(quality), 3),
        "functional_equivalence": round(sum(equivalence) / len(equivalence), 3),
        "ats_composite_delta": round(sum(deltas) / len(deltas), 3),
        "relevance_delta": round(sum(relevance) / len(relevance), 4),
        "negation_failures": sum(len(r["negation_failures"]) for r in records),
        "ablation": aggregate_ablation,
        "task_results": records,
    }
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"jobcard_{results['timestamp']}.json"
        path.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        print(f"\nResults → {path}")
    return results


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true",
                    help="plan with the real TailorPlanner instead of the probe rule")
    ap.add_argument("--no-ablation", action="store_true",
                    help="skip the per-field ablation sweep")
    ap.add_argument("--tasks", nargs="*", default=None, help="task ids to run")
    ap.add_argument("--out", type=Path, default=RESULTS_DIR, help="results directory")
    args = ap.parse_args()

    results = run_eval(task_ids=args.tasks, live=args.live,
                       ablation=not args.no_ablation, out_dir=args.out)

    for record in results["task_results"]:
        print(f"[{'PASS' if record['passed'] else 'FAIL'}] {record['task_id']}  "
              f"quality={record['card_quality']} "
              f"fe={record['functional_equivalence']} "
              f"ats_delta={record['ats_composite_delta']:+} "
              f"relevance_delta={record['relevance_delta']:+}")
        for failure in record["negation_failures"]:
            print(f"       {failure}")

    if results["ablation"]:
        print("\nField ablation — card_quality when the field is dropped "
              "(mean over tasks / worst single task):")
        for field, row in sorted(
            results["ablation"].items(), key=lambda kv: kv[1]["worst_delta"]
        ):
            print(f"  {field:<16} {row['mean_card_quality']:.3f} "
                  f"({row['mean_delta']:+.3f})   "
                  f"worst {row['worst_card_quality']:.3f} "
                  f"({row['worst_delta']:+.3f})   "
                  f"negation_failures={row['negation_failures']}")

    print(f"\ncard_quality: {results['card_quality']} | "
          f"functional_equivalence: {results['functional_equivalence']} | "
          f"ats_delta: {results['ats_composite_delta']:+} | "
          f"relevance_delta: {results['relevance_delta']:+} "
          f"({results['tasks']} task(s), mode={results['mode']})")
    return 0 if all(r["passed"] for r in results["task_results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
