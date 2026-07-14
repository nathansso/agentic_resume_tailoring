"""
Tailoring action planner (issues #91 / #51 Phase 2).

Converts tailoring context into a strict, typed plan of per-item edit actions
instead of an implicit whole-resume rewrite. Each action names WHAT item it
touches, WHICH operation to apply, and WHY:

    {"section": "experience"|"project",
     "item_key": "proj:diginetica-ecomm",
     "op": "keep"|"revise"|"replace"|"delete",
     "strategy": <revision strategy, op=revise only>,
     "keywords": [<JD keywords to weave, strategy=keyword_weave only>],
     "replacement_key": <pool item key, op=replace only>,
     "rationale": "..."}

Ops:
  keep    — pass the item through with its source bullets intact
  revise  — rewrite the item's bullets using a named strategy
  delete  — drop the item from this tailored resume
  replace — swap the item for a candidate from the unselected pool (projects only)

Revision strategies:
  keyword_weave — insert the item's assigned JD keywords where truthful
  quantify      — lead with metrics/numbers already present in the source
  tighten       — compress wording and cut filler, keeping every fact
  reframe       — reorder/reword bullets to lead with the JD-relevant part

The plan is produced by an LLM with a deterministic fallback, then validated
against the real item keys so downstream execution can trust it blindly. Every
executed plan is appended to UserJobResult.tailoring_decisions together with
context features and the achieved reward (ATS composite delta) — those logged
(context, action, propensity, reward) tuples are the training data for the
strategy-knob bandit (issue #51 Phase 2).
"""
import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

OPS = ("keep", "revise", "replace", "delete")
REVISION_STRATEGIES = ("keyword_weave", "quantify", "tighten", "reframe")

# Strategy knobs: the discrete levers a learned policy (issue #51 Phase 2) will
# eventually choose per run. Fixed defaults for now, but every decision-log
# entry records the knob values and their propensities so the log is usable as
# bandit training data from day one.
DEFAULT_KNOBS: Dict = {
    "default_revise_strategy": "keyword_weave",
    "allow_replace": True,
    "allow_delete": True,
}

# Propensity of the logging policy. With fixed knobs the policy is
# deterministic, so every logged action has propensity 1.0; once a bandit
# starts sampling knobs these become the sampling probabilities.
_FIXED_PROPENSITY = 1.0


def _norm_key(value) -> str:
    return str(value or "").strip().lower()


class TailorPlanner:
    """LLM-backed planner with a deterministic fallback.

    Items and pool entries are plain dicts prepared by the caller:
        {"key": "exp:...|proj:...", "section": "experience"|"project",
         "label": <title/name>, "source_text": <bullets/blurbs joined>,
         "suggested_keywords": [...], "relevance": <float, optional>}
    """

    def __init__(self, llm=None):
        # Lazy LLM: constructed on first use so offline paths (tests, stub
        # runs) never touch provider config just to get the fallback plan.
        self._llm = llm

    # ── public API ────────────────────────────────────────────────────────

    def plan(
        self,
        items: List[Dict],
        pool: List[Dict],
        jd_text: str,
        missing_skills: List[str],
        revision_notes: str = "",
        prior_content: Optional[Dict] = None,
        knobs: Optional[Dict] = None,
    ) -> Dict:
        """Return {"actions": [...], "knobs": {...}, "planner": "llm"|"default"}.

        Never raises: any LLM/parse failure degrades to the deterministic
        default plan so tailoring always proceeds.
        """
        knobs = {**DEFAULT_KNOBS, **(knobs or {})}
        if not items:
            return {"actions": [], "knobs": knobs, "planner": "default"}

        raw_actions = None
        try:
            raw_actions = self._llm_plan(
                items, pool, jd_text, missing_skills, revision_notes,
                prior_content, knobs,
            )
        except Exception as exc:
            logger.warning("TailorPlanner LLM plan failed, using default: %s", exc)

        if raw_actions is not None:
            actions = self.validate_plan(raw_actions, items, pool, knobs)
            return {"actions": actions, "knobs": knobs, "planner": "llm"}

        return {
            "actions": self.default_plan(items, knobs),
            "knobs": knobs,
            "planner": "default",
        }

    # ── deterministic fallback ────────────────────────────────────────────

    @staticmethod
    def default_plan(items: List[Dict], knobs: Optional[Dict] = None) -> List[Dict]:
        """One revise action per item: weave its assigned keywords when it has
        any, otherwise tighten. Never deletes or replaces — the fallback must
        be safe to run blind."""
        knobs = {**DEFAULT_KNOBS, **(knobs or {})}
        actions = []
        for item in items:
            kws = list(item.get("suggested_keywords") or [])
            strategy = knobs["default_revise_strategy"] if kws else "tighten"
            actions.append({
                "section": item.get("section"),
                "item_key": item.get("key"),
                "op": "revise",
                "strategy": strategy,
                "keywords": kws,
                "rationale": "default plan: revise with assigned keywords"
                             if kws else "default plan: tighten wording",
                "propensity": _FIXED_PROPENSITY,
            })
        return actions

    # ── validation ────────────────────────────────────────────────────────

    @classmethod
    def validate_plan(
        cls,
        raw_actions: List,
        items: List[Dict],
        pool: List[Dict],
        knobs: Optional[Dict] = None,
    ) -> List[Dict]:
        """Coerce an untrusted action list into a plan execution can rely on:

        - unknown item keys are dropped; duplicate actions keep the first
        - unknown ops/strategies fall back to revise/default strategy
        - replace with a missing/unknown replacement_key degrades to revise
        - replace is projects-only (experiences can't be swapped from a pool)
        - deleting ALL items of a section is refused: the last delete in a
          section becomes keep, so a malformed plan can't empty the resume
        - every input item ends up with exactly one action (missing → default)
        """
        knobs = {**DEFAULT_KNOBS, **(knobs or {})}
        by_key = {_norm_key(i.get("key")): i for i in items}
        pool_keys = {_norm_key(p.get("key")) for p in pool}

        out: Dict[str, Dict] = {}
        for raw in raw_actions if isinstance(raw_actions, list) else []:
            if not isinstance(raw, dict):
                continue
            key = _norm_key(raw.get("item_key") or raw.get("key"))
            if key not in by_key or key in out:
                continue
            item = by_key[key]
            op = _norm_key(raw.get("op"))
            if op not in OPS:
                op = "revise"
            if op == "replace" and not knobs["allow_replace"]:
                op = "revise"
            if op == "delete" and not knobs["allow_delete"]:
                op = "keep"

            action: Dict = {
                "section": item.get("section"),
                "item_key": item.get("key"),
                "op": op,
                "rationale": str(raw.get("rationale") or "").strip()[:300],
                "propensity": _FIXED_PROPENSITY,
            }

            if op == "replace":
                repl = _norm_key(raw.get("replacement_key"))
                if item.get("section") != "project" or repl not in pool_keys:
                    op = action["op"] = "revise"
                else:
                    action["replacement_key"] = repl

            if op == "revise":
                strategy = _norm_key(raw.get("strategy"))
                if strategy not in REVISION_STRATEGIES:
                    strategy = (
                        knobs["default_revise_strategy"]
                        if item.get("suggested_keywords") else "tighten"
                    )
                action["strategy"] = strategy
                kws = raw.get("keywords")
                action["keywords"] = [
                    str(k) for k in (kws if isinstance(kws, list) else [])
                ] or list(item.get("suggested_keywords") or [])

            out[key] = action

        # Fill items the plan didn't cover with the safe default action.
        missing = [i for i in items if _norm_key(i.get("key")) not in out]
        for action in cls.default_plan(missing, knobs):
            out[_norm_key(action["item_key"])] = action

        # Refuse to empty a section: flip the final delete back to keep.
        for section in ("experience", "project"):
            section_keys = [
                _norm_key(i.get("key")) for i in items if i.get("section") == section
            ]
            if section_keys and all(out[k]["op"] == "delete" for k in section_keys):
                survivor = out[section_keys[0]]
                survivor["op"] = "keep"
                survivor["rationale"] = (
                    "coerced from delete: refusing to remove every "
                    f"{section} from the resume"
                )

        # Preserve the caller's item order.
        return [out[_norm_key(i.get("key"))] for i in items]

    # ── LLM planning ──────────────────────────────────────────────────────

    def _get_llm(self):
        if self._llm is None:
            from llm import get_llm
            self._llm = get_llm(role="tailor", temperature=0.0)
        return self._llm

    def _llm_plan(
        self,
        items: List[Dict],
        pool: List[Dict],
        jd_text: str,
        missing_skills: List[str],
        revision_notes: str,
        prior_content: Optional[Dict],
        knobs: Dict,
    ) -> List[Dict]:
        """One LLM call → raw action list (unvalidated). Raises on failure."""
        def item_line(i: Dict) -> Dict:
            return {
                "key": i.get("key"),
                "section": i.get("section"),
                "label": i.get("label"),
                "source_text": (i.get("source_text") or "")[:400],
                "suggested_keywords": i.get("suggested_keywords") or [],
            }

        payload = {
            "items": [item_line(i) for i in items],
            "replacement_pool": [
                {
                    "key": p.get("key"),
                    "label": p.get("label"),
                    "source_text": (p.get("source_text") or "")[:200],
                    "relevance": p.get("relevance"),
                }
                for p in pool[:10]
            ],
            "missing_skills": list(missing_skills or [])[:15],
        }

        prior_block = ""
        if prior_content:
            prior_lines = []
            for e in prior_content.get("experiences") or []:
                prior_lines.append(f"[experience] {e.get('title')}: "
                                   + " | ".join(e.get("bullets") or [])[:300])
            for p in prior_content.get("projects") or []:
                prior_lines.append(f"[project] {p.get('name')}: "
                                   + " | ".join(p.get("bullets") or [])[:300])
            if prior_lines:
                prior_block = (
                    "\n\nCURRENT TAILORED RESUME (source of truth — plan a DELTA "
                    "against this, do not start over):\n" + "\n".join(prior_lines)
                )

        revision_block = (
            f"\n\nUSER REVISION REQUEST (must be reflected in the plan):\n{revision_notes}"
            if revision_notes.strip() else ""
        )

        allowed_ops = ["keep", "revise"]
        if knobs["allow_delete"]:
            allowed_ops.append("delete")
        if knobs["allow_replace"] and payload["replacement_pool"]:
            allowed_ops.append("replace")

        prompt = (
            "You are a resume tailoring PLANNER. Decide a per-item edit plan; "
            "another agent executes it. Respond with ONLY a JSON array.\n\n"
            f"Allowed ops: {', '.join(allowed_ops)}.\n"
            f"Revision strategies: {', '.join(REVISION_STRATEGIES)}.\n"
            "Rules:\n"
            "- Emit exactly one action per item in `items`, keyed by its `key`.\n"
            "- op=replace only for section=project, and replacement_key MUST be "
            "a key from `replacement_pool`. Give the rationale for WHY the "
            "replacement fits this job better.\n"
            "- op=revise must name a strategy; with keyword_weave include the "
            "keywords to weave (prefer the item's suggested_keywords).\n"
            "- op=delete only when an item actively hurts fit for this job.\n"
            "- Every action needs a one-sentence rationale.\n\n"
            f"JOB DESCRIPTION:\n{(jd_text or '')[:2000]}\n\n"
            f"PLANNING INPUT:\n{json.dumps(payload, indent=1)}"
            f"{prior_block}{revision_block}\n\n"
            'Return: [{"item_key": "...", "op": "...", "strategy": "...", '
            '"keywords": [...], "replacement_key": "...", "rationale": "..."}]'
        )

        resp = self._get_llm().invoke([{"role": "user", "content": prompt}])
        raw = (resp.content if hasattr(resp, "content") else str(resp)).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("planner output is not a JSON array")
        return parsed


# ── decision log ──────────────────────────────────────────────────────────

def decision_log_entry(
    plan: Dict,
    context_features: Dict,
    evaluation: Dict,
    revision_notes: str = "",
) -> Dict:
    """One logged (context, actions, reward) tuple for a completed tailoring
    run, appended to UserJobResult.tailoring_decisions. The reward is the
    algorithmic ATS breakdown of the shipped attempt (issue #12), so entries
    are directly comparable across runs."""
    breakdown = (evaluation or {}).get("ats_breakdown") or {}
    reward = {
        "composite": breakdown.get("composite"),
        "baseline_composite": breakdown.get("baseline_composite"),
        "delta": breakdown.get("delta"),
    }
    for component in ("skill_coverage", "keyword_coverage", "section_presence", "role_level"):
        comp = breakdown.get(component)
        if isinstance(comp, dict) and "score" in comp:
            reward[component] = comp["score"]
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "revision_notes": (revision_notes or "").strip(),
        "planner": plan.get("planner"),
        "knobs": plan.get("knobs"),
        "actions": plan.get("actions"),
        "context": context_features,
        "reward": reward,
    }
