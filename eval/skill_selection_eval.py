"""
Skill-selection tuning harness (issue #54, Phase 4).

Offline, LLM-free apparatus for calibrating the skill scorer's weights and cap
bounds. For each checked-in task (a profile's skills + a JD + the JD-relevant
skill names the candidate actually has), it runs rank_and_select_skills() under
several weight presets and reports two quality signals:

  - recall : fraction of JD-relevant skills that survive into the rendered set
             (higher = the tailored skills section keeps what matters)
  - count  : mean number of skills rendered (watch the one-page budget)

This is deliberately self-contained — it does not need the #51 efficacy
pipeline. When #51's task dataset lands, point `load_tasks()` at it (or add the
composite-delta metric) to calibrate against real baseline→tailored deltas.

Usage:
    python eval/skill_selection_eval.py
"""
import json
import sys
from pathlib import Path
from statistics import mean
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.skill_scorer import WEIGHTS, rank_and_select_skills, selection_recall  # noqa: E402

TASKS_DIR = Path(__file__).resolve().parent / "skill_selection_tasks"

# Weight presets to compare. The offline harness has no embeddings, so the
# 'semantic' weight is inert here; presets vary the lexical/metadata mix.
PRESETS: Dict[str, Dict] = {
    "default": dict(WEIGHTS),
    "lexical_heavy": {**WEIGHTS, "tfidf": 0.40, "jd_weight": 0.10, "proficiency": 0.05},
    "jd_weight_heavy": {**WEIGHTS, "jd_weight": 0.40, "tfidf": 0.10},
    "proficiency_heavy": {**WEIGHTS, "proficiency": 0.30, "tfidf": 0.10},
}


def load_tasks() -> List[Dict]:
    if not TASKS_DIR.exists():
        return []
    tasks = []
    for path in sorted(TASKS_DIR.glob("*.json")):
        tasks.append(json.loads(path.read_text(encoding="utf-8")))
    return tasks


def evaluate_preset(tasks: List[Dict], weights: Dict) -> Dict:
    recalls, counts = [], []
    for task in tasks:
        selected = rank_and_select_skills(
            task["skills"],
            task["jd_text"],
            task.get("matched_skills") or {},
            corpus_texts=task.get("corpus") or [],
            weights=weights,
        ) or []
        recalls.append(selection_recall(selected, task.get("relevant") or []))
        counts.append(len(selected))
    return {
        "mean_recall": round(mean(recalls), 3) if recalls else 0.0,
        "mean_count": round(mean(counts), 1) if counts else 0.0,
    }


def main() -> int:
    tasks = load_tasks()
    if not tasks:
        print(f"No tasks found under {TASKS_DIR}. Add <name>.json fixtures to calibrate.")
        return 1

    print(f"Skill-selection tuning over {len(tasks)} task(s)\n")
    print(f"{'preset':<20}{'mean_recall':>14}{'mean_count':>14}")
    print("-" * 48)
    for name, weights in PRESETS.items():
        m = evaluate_preset(tasks, weights)
        print(f"{name:<20}{m['mean_recall']:>14}{m['mean_count']:>14}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
