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

By default the policy is deterministic, so every logged propensity is 1.0 and
the log has no action variance to learn from. Setting TAILOR_EXPLORATION_MODE
switches on ε-greedy sampling over the `strategy` field (issue #112) and, in
lockstep, suspends the best-of-N retry loop — see exploration_mode() for why
the two cannot move independently.
"""
import json
import logging
import os
import random
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from agents.skill_scorer import _env_float

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

# Propensity of a deterministic decision: the greedy (non-exploring) policy
# picks its action with probability 1. Exploration replaces this with the real
# sampling probability for the `strategy` field only — see _choose_strategy.
_DETERMINISTIC_PROPENSITY = 1.0


def exploration_mode() -> bool:
    """Whether this process collects ε-greedy exploration data (issue #112).

    Off by default. Turning it on does two things *together*, and they must
    never move independently: revision strategies are sampled ε-greedily, and
    the best-of-N retry loop is suspended (N=1, see agents.tailor). Exploration
    without N=1 logs rewards confounded by the max-order statistic and by the
    endogenous attempt count; N=1 without exploration is a pure regression of
    issue #58's "never ship a worse output" guarantee for no learning gain.

    Read from the environment per call rather than cached at import so tests
    and a running server can flip it without a reload.
    """
    return os.environ.get("TAILOR_EXPLORATION_MODE", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def explore_epsilon() -> float:
    """Probability of sampling a strategy uniformly instead of taking the
    greedy arm. At ε=0.2 with ~6 items roughly one item per run gets a
    non-greedy strategy — the intended cost of exploration."""
    return _env_float("TAILOR_EXPLORE_EPSILON", 0.2)


def _norm_key(value) -> str:
    return str(value or "").strip().lower()


def _greedy_strategy(item: Dict, knobs: Dict) -> str:
    """The strategy today's deterministic policy would pick for this item.

    Context-dependent: items carrying assigned JD keywords weave them, items
    without tighten. Propensity must be computed against the arm that is
    greedy *for that item*, not against a single global default.
    """
    return knobs["default_revise_strategy"] if item.get("suggested_keywords") else "tighten"


def _strategy_propensity(strategy: str, greedy: str, epsilon: float) -> float:
    """P(strategy) under ε-greedy over REVISION_STRATEGIES. Sums to 1 over the
    strategy set: the greedy arm can be reached by exploiting or by exploring
    onto itself, every other arm only by exploring."""
    n = len(REVISION_STRATEGIES)
    if strategy == greedy:
        return 1.0 - epsilon + epsilon / n
    return epsilon / n


def _choose_strategy(
    item: Dict,
    knobs: Dict,
    rng: Optional[random.Random] = None,
    explore: bool = False,
) -> Tuple[str, float]:
    """Pick a revision strategy for one item and return (strategy, propensity).

    Single source of truth for the strategy decision: both default_plan() and
    validate_plan() route through here so the logged propensity can never drift
    from the distribution that actually produced the action. Sampling is per
    item and independent, so each action carries its own propensity — the right
    granularity for per-edit reward attribution (issue #113).
    """
    greedy = _greedy_strategy(item, knobs)
    if not explore:
        return greedy, _DETERMINISTIC_PROPENSITY
    epsilon = explore_epsilon()
    rng = rng or random.Random()
    if rng.random() < epsilon:
        strategy = REVISION_STRATEGIES[rng.randrange(len(REVISION_STRATEGIES))]
    else:
        strategy = greedy
    return strategy, _strategy_propensity(strategy, greedy, epsilon)


class TailorPlanner:
    """LLM-backed planner with a deterministic fallback.

    Items and pool entries are plain dicts prepared by the caller:
        {"key": "exp:...|proj:...", "section": "experience"|"project",
         "label": <title/name>, "source_text": <bullets/blurbs joined>,
         "suggested_keywords": [...], "relevance": <float, optional>}
    """

    def __init__(self, llm=None, rng: Optional[random.Random] = None):
        # Lazy LLM: constructed on first use so offline paths (tests, stub
        # runs) never touch provider config just to get the fallback plan.
        self._llm = llm
        # Injected so exploration is reproducible under a seed; never call the
        # module-level `random` functions, which no test can pin.
        self._rng = rng or random.Random()

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
        allow_explore: bool = True,
    ) -> Dict:
        """Return {"actions": [...], "knobs": {...}, "planner": "llm"|"default"}.

        Never raises: any LLM/parse failure degrades to the deterministic
        default plan so tailoring always proceeds.

        *allow_explore* lets a caller opt out of ε-greedy sampling even in
        exploration mode; plan_preview() sets it False because a previewed plan
        is shown to a human for approval, which takes it off-policy anyway.
        """
        knobs = {**DEFAULT_KNOBS, **(knobs or {})}
        # Never explore against a user's explicit revision request: sampling
        # `tighten` when someone asked for more numbers is user-hostile.
        explore = (
            allow_explore
            and exploration_mode()
            and not (revision_notes or "").strip()
        )
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
            actions = self.validate_plan(
                raw_actions, items, pool, knobs, rng=self._rng, explore=explore,
            )
            return {"actions": actions, "knobs": knobs, "planner": "llm"}

        return {
            "actions": self.default_plan(items, knobs, rng=self._rng, explore=explore),
            "knobs": knobs,
            "planner": "default",
        }

    # ── deterministic fallback ────────────────────────────────────────────

    @staticmethod
    def default_plan(
        items: List[Dict],
        knobs: Optional[Dict] = None,
        rng: Optional[random.Random] = None,
        explore: bool = False,
    ) -> List[Dict]:
        """One revise action per item: weave its assigned keywords when it has
        any, otherwise tighten. Never deletes or replaces — the fallback must
        be safe to run blind. Under *explore* the strategy is sampled ε-greedily
        around that rule instead (issue #112)."""
        knobs = {**DEFAULT_KNOBS, **(knobs or {})}
        actions = []
        for item in items:
            kws = list(item.get("suggested_keywords") or [])
            strategy, propensity = _choose_strategy(item, knobs, rng, explore)
            actions.append({
                "section": item.get("section"),
                "item_key": item.get("key"),
                "label": item.get("label"),
                "op": "revise",
                "strategy": strategy,
                "strategy_source": "sampled" if explore else "default",
                "keywords": kws,
                "rationale": "default plan: revise with assigned keywords"
                             if kws else "default plan: tighten wording",
                "propensity": propensity,
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
        rng: Optional[random.Random] = None,
        explore: bool = False,
    ) -> List[Dict]:
        """Coerce an untrusted action list into a plan execution can rely on:

        - unknown item keys are dropped; duplicate actions keep the first
        - unknown ops/strategies fall back to revise/default strategy
        - replace with a missing/unknown replacement_key degrades to revise
        - replace is projects-only (experiences can't be swapped from a pool)
        - deleting ALL items of a section is refused: the last delete in a
          section becomes keep, so a malformed plan can't empty the resume
        - every input item ends up with exactly one action (missing → default)

        Under *explore* the sampler overrides the LLM's `strategy` for revise
        actions (issue #112). That override is the whole point: logging a
        propensity for a decision the LLM made would attribute a known density
        to a distribution we cannot observe. The LLM stays the proposal
        distribution for op/replacement_key/keywords, and its own strategy pick
        is retained as `llm_strategy` — free off-policy data on the model's
        implicit policy.
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
                "label": item.get("label"),
                "op": op,
                "rationale": str(raw.get("rationale") or "").strip()[:300],
                # Ops stay with the planner — exploration is scoped to the
                # `strategy` field, so a non-revise action is deterministic
                # from the policy's point of view. The revise branch below
                # overwrites this with the real sampling probability.
                "propensity": _DETERMINISTIC_PROPENSITY,
            }

            if op == "replace":
                repl = _norm_key(raw.get("replacement_key"))
                if item.get("section") != "project" or repl not in pool_keys:
                    op = action["op"] = "revise"
                else:
                    action["replacement_key"] = repl

            if op == "revise":
                llm_strategy = _norm_key(raw.get("strategy"))
                if llm_strategy not in REVISION_STRATEGIES:
                    llm_strategy = None
                if explore:
                    strategy, action["propensity"] = _choose_strategy(
                        item, knobs, rng, True
                    )
                    action["strategy_source"] = "sampled"
                elif llm_strategy:
                    strategy = llm_strategy
                    action["strategy_source"] = "llm"
                else:
                    strategy, action["propensity"] = _choose_strategy(item, knobs, rng)
                    action["strategy_source"] = "default"
                action["strategy"] = strategy
                if llm_strategy:
                    action["llm_strategy"] = llm_strategy
                kws = raw.get("keywords")
                action["keywords"] = [
                    str(k) for k in (kws if isinstance(kws, list) else [])
                ] or list(item.get("suggested_keywords") or [])

            out[key] = action

        # Fill items the plan didn't cover with the safe default action.
        missing = [i for i in items if _norm_key(i.get("key")) not in out]
        for action in cls.default_plan(missing, knobs, rng=rng, explore=explore):
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
            line = {
                "key": i.get("key"),
                "section": i.get("section"),
                "label": i.get("label"),
                "source_text": (i.get("source_text") or "")[:400],
                "suggested_keywords": i.get("suggested_keywords") or [],
            }
            # KG evidence: JD skills this item is tied to through the knowledge
            # graph (issue #138). Included only when present, so a sparse graph
            # leaves the planning payload byte-for-byte unchanged.
            if i.get("graph_evidence"):
                line["graph_evidence"] = list(i["graph_evidence"])
            return line

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
            "- An item may carry `graph_evidence`: JD skills it is tied to through "
            "the candidate's knowledge graph (shared skills across their "
            "projects/experience), even when the item's own text does not name "
            "them. Treat it as strong evidence the item is relevant to this job: "
            "prefer keep/revise over delete, and do NOT replace an item that "
            "uniquely evidences a required skill.\n"
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
    exploration_mode: bool = False,
) -> Dict:
    """One logged (context, actions, reward) tuple for a completed tailoring
    run, appended to UserJobResult.tailoring_decisions. The reward is the
    algorithmic ATS breakdown of the shipped attempt (issue #12), so entries
    are directly comparable across runs.

    *exploration_mode* and the derived *n_attempts* are recorded explicitly so
    mixed-mode data stays separable: a reward logged under best-of-N is the max
    over a run-dependent number of draws, not a sample of E[reward | plan], and
    must never be pooled with N=1 exploration data (issue #112)."""
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
        "exploration_mode": bool(exploration_mode),
        "n_attempts": (context_features or {}).get("attempts"),
        "knobs": plan.get("knobs"),
        "actions": plan.get("actions"),
        "context": context_features,
        "reward": reward,
    }
