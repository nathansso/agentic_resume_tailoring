"""Preference-vs-JD arbitration (issue #129).

Tailoring is the arbitration between what the job wants and what the candidate
wants. After #121 only the job side existed as an artifact; `agents/
preferences.py` supplies the candidate side, and this module resolves the two
into a single constraint set the planner is gated on.

Pure and deterministic by construction — no LLM, no database, no clock. That is
not stylistic: the acceptance criterion is that two runs over the same
`(PreferenceProfile, JDProfile, KG)` produce an identical constraint set, and a
pure function makes that a one-line assertion rather than a hope about
temperature. It also keeps the arbitration honest under review: every rule below
is readable in one file, which is the whole point of not letting this decision
live inside a prompt.

The precedence chain, in order:

1. **Truthfulness first.** An `emphasize` naming something the knowledge graph
   does not hold is *refused* — it does not enter arbitration at all, it is
   recorded in `refused[]` and dropped. This is the fabrication boundary and it
   is not negotiable against anything. `FAITHFULNESS_MIN` is untouched: this
   refuses the instruction up front rather than relaxing the check downstream.
2. **Hard preferences (`strength` 5) always win**, and the requirement they
   block is *reported*, never silently dropped. "Never mention my current
   employer" is honored at ATS cost, but the cost is made visible.
3. **Soft preferences (1-4) are weighed against `criticality`.** The preference
   loses when the requirement is at least as central as the preference is firm.
4. **Ties resolve toward the JD** — the artifact's purpose is getting the
   interview. Combined with rule 3 that makes the test `strength > criticality`.

**Only `suppress` is arbitrated against requirements.** `emphasize` and
`reframe` cannot remove a requirement's evidence from the resume — they promote
or rewrite content that stays — so there is nothing for a requirement to
contest. Suppression removes, which is precisely the case where honoring the
user can cost them the match. Narrowing arbitration to the polarity that can
actually do damage keeps the conflict report meaningful instead of filling it
with pairs no human would call a conflict.

A visible conflict surface is a requirement, not polish: a preference tier that
silently suppresses a critical requirement is #121's failure mode ("a preferred
mis-parsed as required biases every decision with no visible symptom")
reproduced on the candidate side.
"""
import logging
from typing import Dict, List, Optional, Sequence

from agents.jd_profile import iter_requirements
from agents.preferences import terms_of

logger = logging.getLogger(__name__)

# Requirement types that can contest a preference. `incidental` is by definition
# not a qualification, so suppressing something it mentions costs nothing.
_BINDING_TYPES = ["required", "preferred"]

# Hard preference: honored even against a maximally critical requirement.
HARD_STRENGTH = 5

# Subject tokens too generic to match a requirement on. Kept deliberately tiny —
# this is a guard against matching on "the", not a stopword list, and every
# entry added here is a term that can no longer be suppressed by name.
_GENERIC = {
    "the", "and", "for", "with", "that", "this", "job", "role", "resume",
    "about", "from", "into", "have", "has", "was", "not", "any", "all",
}

REASON_UNSUPPORTED = "unsupported_by_knowledge_graph"


def _norm(value) -> str:
    return str(value or "").strip().lower()


def _subject(pref: Dict) -> str:
    """What the preference is about, as free text."""
    return _norm(pref.get("target_term") or pref.get("text"))


def _subject_tokens(pref: Dict) -> set:
    """Comparable tokens from the preference's subject.

    Single characters are dropped: a bare "r" or "c" token matches far more text
    than it should, and the cost is that a preference cannot be matched to a
    one-letter language name. Two characters are kept, so "ml", "go" and "ai"
    still work.
    """
    return {
        t for t in terms_of(_subject(pref))
        if len(t) >= 2 and t not in _GENERIC
    }


def matching_requirements(pref: Dict, jd_payload: Optional[Dict]) -> List[Dict]:
    """JD requirements this preference contests, in source order.

    Deterministic term overlap, never embeddings. That is not a shortcut taken
    for cost: ImplexConv finding 4 measures similarity retrieval failing on
    exactly this data (RAG 12.7%, GraphRAG 5.6% on opposed cases), so the cheap
    implementation is also the correct one here.

    Matching is **token-level, never substring**. A raw `term in subject` test
    reads as reasonable and silently matches "ml" inside "html", which
    suppresses the wrong resume item with no visible symptom. So a one-word term
    must equal a subject token, and a phrase must either appear verbatim in the
    subject or have all of its tokens present.
    """
    subject = _subject(pref)
    tokens = _subject_tokens(pref)
    if not subject or not tokens:
        return []
    hits: List[Dict] = []
    for req in iter_requirements(jd_payload, types=_BINDING_TYPES):
        for term in req.get("terms") or []:
            term_norm = _norm(term)
            term_tokens = terms_of(term_norm)
            if not term_tokens:
                continue
            if len(term_tokens) == 1:
                matched = term_tokens[0] in tokens
            else:
                matched = term_norm in subject or set(term_tokens) <= tokens
            if matched:
                hits.append(req)
                break
    return hits


def _is_supported(pref: Dict, supported_keys: Sequence[str]) -> bool:
    """Whether the knowledge graph holds what this preference wants emphasized.

    Requires a *resolved* target key. An emphasize whose target never bound to a
    graph item ("lead with my leadership") has no evidence behind it, and
    honoring it would mean the generator inventing the content to lead with —
    the fabrication this refusal exists to prevent.
    """
    key = pref.get("target_key")
    return bool(key) and key in set(supported_keys or ())


def compile_constraints(
    preferences: Sequence[Dict],
    jd_payload: Optional[Dict] = None,
    supported_keys: Optional[Sequence[str]] = None,
) -> Dict:
    """Resolve preferences against the JD into `{applied, conflicts, refused}`.

    *preferences* must already be scope-filtered (see
    `preferences.preferences_in_scope`). *supported_keys* are the knowledge-graph
    item keys available to this run. *jd_payload* absent means no requirement can
    contest anything, so every non-refused preference applies — which is the
    correct behavior for a job whose profile has not been extracted, not a
    degradation.

    Empty in, empty out: no preferences yields `{"applied": [], "conflicts": [],
    "refused": []}`, and every consumer treats that as "unchanged from before
    this issue".
    """
    applied: List[Dict] = []
    conflicts: List[Dict] = []
    refused: List[Dict] = []

    ordered = sorted(
        list(preferences or []), key=lambda p: str(p.get("preference_id") or ""))

    for pref in ordered:
        polarity = _norm(pref.get("polarity")) or "suppress"
        strength = int(pref.get("strength") or 3)
        entry = {
            "preference_id": str(pref.get("preference_id") or ""),
            "text": pref.get("text") or "",
            "polarity": polarity,
            "target_key": pref.get("target_key"),
            "target_term": pref.get("target_term"),
            "strength": strength,
        }
        # The semantic-tier trait this preference belongs to (issue #133).
        # Carried through so it reaches the decision log, where #119 conditions
        # on it as a context bucket. Absent for a caller that supplied raw
        # leaves rather than a compiled persona, which keeps this module usable
        # on its own and keeps the empty-constraint path untouched.
        if pref.get("trait_key"):
            entry["trait_key"] = pref["trait_key"]

        # 1. Truthfulness first — refused outright, never arbitrated.
        if polarity == "emphasize" and not _is_supported(pref, supported_keys or []):
            refused.append({
                **entry,
                "reason": REASON_UNSUPPORTED,
                "detail": (
                    "Nothing in your profile evidences this, so honoring it "
                    "would mean inventing content."
                ),
            })
            continue

        # Only suppression can cost the candidate a requirement (see module doc).
        if polarity != "suppress":
            applied.append(entry)
            continue

        hits = matching_requirements(pref, jd_payload)
        if not hits:
            applied.append(entry)
            continue

        contested = max(hits, key=lambda r: int(r.get("criticality") or 3))
        criticality = int(contested.get("criticality") or 3)
        # 2/3/4: hard preferences always win; otherwise the preference must be
        # strictly firmer than the requirement is central, so ties go to the JD.
        preference_wins = strength >= HARD_STRENGTH or strength > criticality

        conflicts.append({
            **entry,
            "requirement_text": contested.get("text") or "",
            "requirement_type": contested.get("type") or "",
            "criticality": criticality,
            "winner": "preference" if preference_wins else "jd",
            "effect": (
                "Honored — the job asks for this and it was left out anyway."
                if preference_wins else
                "Overridden — the job requires this, so it was kept."
            ),
        })
        if preference_wins:
            applied.append({**entry, "contested_requirement": contested.get("text")})

    return {"applied": applied, "conflicts": conflicts, "refused": refused}


def is_empty(constraints: Optional[Dict]) -> bool:
    """Whether this constraint set changes anything.

    The condition every conditional-inclusion site branches on, so "no
    preferences leaves the planner payload byte-for-byte unchanged" has exactly
    one definition.
    """
    c = constraints or {}
    return not (c.get("applied") or c.get("conflicts") or c.get("refused"))


def applied_by_polarity(constraints: Optional[Dict], polarity: str) -> List[Dict]:
    return [
        a for a in (constraints or {}).get("applied") or []
        if _norm(a.get("polarity")) == polarity
    ]


_VERBS = (
    ("suppress", "LEAVE OUT"),
    ("emphasize", "LEAD WITH"),
    ("reframe", "REFRAME"),
)


def _constraint_line(item: Dict, verb: str) -> str:
    subject = item.get("target_term") or item.get("target_key") or ""
    suffix = f" [{subject}]" if subject else ""
    return f"- {verb}{suffix}: {item.get('text')}"


def render_constraints(
    constraints: Optional[Dict], traits: Optional[Sequence[Dict]] = None,
) -> str:
    """The constraint set as planner prompt text. Empty set → empty string.

    This is the *proposal*-side half only. The prompt makes the planner likelier
    to propose a compliant plan; `tailor_planner.apply_constraints` is what makes
    non-compliance impossible. ImplexConv finding 2 is why both exist and why
    neither is sufficient alone: retrieval of the invalidating fact succeeds and
    the model still reasons past it, so a prompt cannot be the enforcement.

    *traits* is the #133 semantic tier. When supplied, the applied constraints
    are grouped under their trait label, so the planner reads a standing
    disposition ("Downplays projects when targeting ML roles") with its evidence
    beneath it rather than three unrelated-looking lines. This is the only place
    traits affect anything the planner sees: arbitration still reads leaves, and
    `apply_constraints` gates on leaves, so the plan is provably unchanged by
    the grouping — only the proposal distribution moves.

    **Omitting *traits* renders byte-identically to pre-#133**, which is what
    keeps this module independently testable and every existing caller correct.
    """
    if is_empty(constraints):
        return ""
    if not traits:
        lines: List[str] = []
        for polarity, verb in _VERBS:
            for item in applied_by_polarity(constraints, polarity):
                lines.append(_constraint_line(item, verb))
        return "\n".join(lines)

    verb_of = dict(_VERBS)
    applied = {
        str(a.get("preference_id") or ""): a
        for a in (constraints or {}).get("applied") or []
    }
    grouped: List[str] = []
    claimed: set = set()
    for trait in traits:
        block: List[str] = []
        for leaf_id in trait.get("leaf_ids") or []:
            item = applied.get(str(leaf_id))
            if item is None:
                continue
            verb = verb_of.get(_norm(item.get("polarity")))
            if verb is None:
                continue
            block.append(_constraint_line(item, verb))
            claimed.add(str(leaf_id))
        if block:
            grouped.append(f"{trait.get('label') or 'Standing preference'}:")
            grouped.extend(block)

    # Anything the traits did not cover still has to reach the prompt. A
    # constraint silently dropped because its trait link went missing is the
    # failure mode this tier is supposed to remove, not introduce.
    for polarity, verb in _VERBS:
        for item in applied_by_polarity(constraints, polarity):
            if str(item.get("preference_id") or "") not in claimed:
                grouped.append(_constraint_line(item, verb))
    return "\n".join(grouped)
