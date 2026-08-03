"""Persona semantic tier — a lossless 1-level index over the #129 leaves (issue #133).

#129 shipped the episodic tier: one `UserPreference` row per standing preference,
extracted from chat, arbitrated against the JD, gated onto the plan. What it did
not ship is any abstraction *over* those leaves. This module is that abstraction,
and it is deliberately the smallest one that works: **one level, and nothing is
summarized away.**

**Why lossless index rather than lossy summary.** The two published options are
genuinely different and the choice is forced by this data. PersonaAgent
(arXiv:2506.06254) distils the last *n* interactions into the persona prompt,
*replacing* the raw facts. ImplexConv/TaciTree (arXiv:2503.07018) keeps the
leaves and lets cluster summaries route to them. ARTie's dominant preference
signal is **negation** — "that was just coursework", "stop leading with X" — and
TaciTree measures implicit-evidence retrieval at 55.2% F1 supportive versus
**14.8% opposed**. The opposed case is where every method fails and it is our
majority case, so a fixed-size summary would drop exactly the suppressions that
matter most. Traits therefore point *down* at leaves and never hold content of
their own; `arbitration.compile_constraints` still reads the leaves.

**Why one level.** At realistic N — tens of preferences per user — TaciTree's own
`H^j = floor(H^(j-1)/k)` collapses to two levels anyway, so recursion would buy
structure that never branches.

**Why deterministic grouping rather than clustering.** Trait membership is a
downstream RL *context feature* (#119). Emergent clustering over a small,
incrementally-growing set re-shuffles assignments on nearly every insert, which
would churn trait ids and make the policy's context buckets non-stationary.
Keying the trait on typed leaf fields keeps trait identity, provenance links, and
RL buckets stable across recomputes — which is also what makes "two compiles
produce byte-identical output" a property of the code rather than a hope.

**Why the label is a template and not a model call.** The issue's 2026-07-23
infra note specified a cached classify against a fixed taxonomy enum. The group
key is itself deterministic and low-cardinality, so the classify would buy a
nicer phrase at the cost of making the determinism criterion a claim about a
cache rather than about the code. `trait_label` is a pure function of the key.

Pure by construction — no LLM, no database, no clock — matching
`agents/arbitration.py` and `agents/job_card.py`. Persistence and the
event-driven rebuild live in `services.rebuild_persona`, and the write barrier
there is what keeps the pipeline from authoring the user's own tier.

The compiled persona is the **JD-independent** half of the constraint story;
`arbitration.compile_constraints` remains the JD-dependent half and is fed by
this module rather than replaced by it.
"""
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from agents.preferences import (
    POLARITIES,
    SCOPE_TYPES,
    STATUS_ACTIVE,
    TARGET_TYPES,
    preferences_in_scope,
)
from agents.extraction_schemas import (
    PreferencePolarity,
    PreferenceScopeType,
    PreferenceTargetType,
)

logger = logging.getLogger(__name__)

# Bump when the grouping rule or the compiled shape changes, so a stored persona
# compiled under an older rule is rebuilt rather than read with the wrong keys.
PERSONA_VERSION = 1

TRAIT_ACTIVE = "active"
TRAIT_INACTIVE = "inactive"

# ── label templates ──────────────────────────────────────────────────────────
#
# Every phrase a user reads about their own persona is assembled from these
# three tables. Keeping them literal — rather than deriving prose from a model —
# is what makes `trait_label` a pure function of `group_key`.

_POLARITY_VERB = {
    PreferencePolarity.SUPPRESS.value: "Downplays",
    PreferencePolarity.EMPHASIZE.value: "Leads with",
    PreferencePolarity.REFRAME.value: "Reframes",
}

_TARGET_NOUN = {
    PreferenceTargetType.EXPERIENCE.value: "work experience",
    PreferenceTargetType.PROJECT.value: "projects",
    PreferenceTargetType.SKILL.value: "skills",
    PreferenceTargetType.SECTION.value: "resume sections",
    PreferenceTargetType.TOPIC.value: "topics",
}

_SCOPE_CLAUSE = {
    PreferenceScopeType.GLOBAL.value: "on every resume",
    PreferenceScopeType.JOB.value: "on individual applications",
}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _enum_or(value: Any, allowed: Sequence[str], default: str) -> str:
    """A stored enum field, coerced to a known value.

    Rows predating a value, or hand-edited through the API, must not be able to
    invent a new trait bucket — an unknown polarity silently becoming its own
    group would fragment the index and break the "exactly one trait" invariant.
    """
    normalized = _norm(value).replace("-", "_").replace(" ", "_")
    return normalized if normalized in allowed else default


# ── deterministic grouping ───────────────────────────────────────────────────

def group_key(pref: Dict) -> str:
    """The trait this preference belongs to. Pure, total, and stable.

    `polarity|target_type|scope_type`, plus `:scope_value` for `role_family`
    only. That last exclusion is the whole design of the tier in one line:

    - **`role_family` carries its value** because "downplays projects when
      targeting ML roles" and "downplays projects when targeting research roles"
      are two different dispositions the user genuinely holds separately.
    - **`job` does not**, because keying on the job id would mint one trait per
      application and make the index vacuous — every trait would have exactly
      one leaf, and a per-leaf "grouping" indexes nothing.

    A trait is a standing disposition, so it groups *across* the jobs that
    evidenced it. The individual job scoping is not lost: it stays on each leaf
    and is what `preferences_in_scope` filters on at planning time.
    """
    polarity = _enum_or(
        pref.get("polarity"), POLARITIES, PreferencePolarity.SUPPRESS.value)
    target_type = _enum_or(
        pref.get("target_type"), TARGET_TYPES, PreferenceTargetType.TOPIC.value)
    scope_type = _enum_or(
        pref.get("scope_type"), SCOPE_TYPES, PreferenceScopeType.JOB.value)
    key = f"{polarity}|{target_type}|{scope_type}"
    if scope_type == PreferenceScopeType.ROLE_FAMILY.value:
        family = _norm(pref.get("scope_value"))
        key = f"{key}:{family}" if family else key
    return key


def parse_group_key(key: str) -> Dict:
    """`group_key` inverted into its typed parts.

    Stored decomposed on `PersonaTrait` as well, so a query filters without
    parsing; this exists so the label and the row can both be derived from the
    key alone and cannot disagree.
    """
    raw = str(key or "")
    scope_value = None
    if ":" in raw:
        raw, scope_value = raw.split(":", 1)
        scope_value = scope_value or None
    parts = raw.split("|")
    parts += [""] * (3 - len(parts))
    return {
        "polarity": _enum_or(
            parts[0], POLARITIES, PreferencePolarity.SUPPRESS.value),
        "target_type": _enum_or(
            parts[1], TARGET_TYPES, PreferenceTargetType.TOPIC.value),
        "scope_type": _enum_or(
            parts[2], SCOPE_TYPES, PreferenceScopeType.JOB.value),
        "scope_value": scope_value,
    }


def trait_label(key: str) -> str:
    """Human-readable statement of the trait. Pure function of the key.

    Deliberately reads as a claim about the user ("Downplays projects when
    targeting machine-learning roles") rather than as a rule, because that is
    what the inspect surface asks them to confirm or correct. A label the user
    disagrees with is the cheapest possible signal that an extraction went
    wrong, and criterion 9 is that they can fix it.
    """
    parts = parse_group_key(key)
    verb = _POLARITY_VERB.get(parts["polarity"], "Applies to")
    noun = _TARGET_NOUN.get(parts["target_type"], "topics")
    scope_type = parts["scope_type"]
    if scope_type == PreferenceScopeType.ROLE_FAMILY.value and parts["scope_value"]:
        family = parts["scope_value"].replace("_", "-")
        clause = f"when targeting {family} roles"
    else:
        clause = _SCOPE_CLAUSE.get(scope_type, _SCOPE_CLAUSE[
            PreferenceScopeType.JOB.value])
    return f"{verb} {noun} {clause}"


def _leaf_id(pref: Dict) -> str:
    return str(pref.get("preference_id") or "")


def _active(leaves: Sequence[Dict]) -> List[Dict]:
    return [
        p for p in leaves or []
        if (p.get("status") or STATUS_ACTIVE) == STATUS_ACTIVE and _leaf_id(p)
    ]


def build_traits(active_leaves: Sequence[Dict]) -> List[Dict]:
    """Group active leaves into traits. Pure and order-independent.

    Determinism comes from sorting, not from insertion order: traits sort by
    `group_key` and `leaf_ids` sort by preference id, so the same set of leaves
    arriving in any order produces the identical structure. That is what the
    determinism criterion is actually asserting — a test that fed the leaves in
    the same order twice would be a tautology.

    **The lossless invariant lives here.** Every active leaf lands in exactly one
    trait, because `group_key` is total (it coerces unknown enum values rather
    than rejecting them) and the grouping is a plain partition. No leaf content
    is copied up: a trait holds ids and nothing else, so there is no way for the
    index to disagree with what it indexes.
    """
    buckets: Dict[str, List[str]] = {}
    for pref in _active(active_leaves):
        buckets.setdefault(group_key(pref), []).append(_leaf_id(pref))

    traits: List[Dict] = []
    for key in sorted(buckets):
        parts = parse_group_key(key)
        traits.append({
            "group_key": key,
            "label": trait_label(key),
            **parts,
            "leaf_ids": sorted(set(buckets[key])),
        })
    return traits


def superseded_links(leaves: Sequence[Dict]) -> Dict[str, List[str]]:
    """`group_key` -> ids of leaves that are no longer active. Pure.

    The half of supersession #129 could not do. #129 keeps the superseded *row*;
    this keeps the *link*, so a trait remains a full record of the disposition's
    trajectory rather than only its current state. That history is what #51
    Phase 2 learns preference weights from, and dropping it would make the tier a
    lossy view of a trajectory the decision log itself retains.

    Deliberately not part of `compile_persona`: the compiled persona must be a
    pure function of the *active* leaves (criterion 5), and folding inactive ones
    into it would make the digest change every time a preference was retracted
    without the effective constraint set changing at all.
    """
    out: Dict[str, List[str]] = {}
    for pref in leaves or []:
        if (pref.get("status") or STATUS_ACTIVE) == STATUS_ACTIVE:
            continue
        leaf = _leaf_id(pref)
        if leaf:
            out.setdefault(group_key(pref), []).append(leaf)
    return {k: sorted(set(v)) for k, v in sorted(out.items())}


# ── compiled persona ─────────────────────────────────────────────────────────

def _constraint_entry(pref: Dict, key: str) -> Dict:
    """One leaf as a codified constraint.

    The field set is **exactly what `arbitration.compile_constraints` already
    consumes**, plus `trait_key` and the scope/status fields
    `preferences.preferences_in_scope` filters on. Matching the existing shape is
    the point: this tier feeds the #129 compile rather than opening a second path
    into the planner, so there is one place where a preference becomes a
    constraint and one place it can be wrong.
    """
    return {
        "preference_id": _leaf_id(pref),
        "text": pref.get("text") or "",
        "polarity": _enum_or(
            pref.get("polarity"), POLARITIES, PreferencePolarity.SUPPRESS.value),
        "target_type": _enum_or(
            pref.get("target_type"), TARGET_TYPES,
            PreferenceTargetType.TOPIC.value),
        "target_key": pref.get("target_key"),
        "target_term": pref.get("target_term"),
        "strength": pref.get("strength"),
        "scope_type": _enum_or(
            pref.get("scope_type"), SCOPE_TYPES, PreferenceScopeType.JOB.value),
        "scope_value": pref.get("scope_value"),
        "status": STATUS_ACTIVE,
        "trait_key": key,
    }


def compile_persona(active_leaves: Sequence[Dict]) -> Dict:
    """Active leaves -> `{version, traits, constraints}`. Pure and deterministic.

    The codified persona: framing and selection constraints, and nothing that
    could be mistaken for a fact. The knowledge graph holds what is true; this
    holds how the user wants what is true to be chosen and worded. That boundary
    is enforced above this module — `arbitration` refuses an `emphasize` the
    graph does not support, and `FAITHFULNESS_MIN` sits above that — so nothing
    here needs to, and nothing here may relax it.

    Empty in, empty out: no active leaves yields empty `traits` and `constraints`,
    which every consumer already treats as "unchanged from before this issue".
    """
    traits = build_traits(active_leaves)
    by_id = {_leaf_id(p): p for p in _active(active_leaves)}
    constraints: List[Dict] = []
    for trait in traits:
        for leaf_id in trait["leaf_ids"]:
            pref = by_id.get(leaf_id)
            if pref is not None:
                constraints.append(_constraint_entry(pref, trait["group_key"]))
    constraints.sort(key=lambda c: str(c.get("preference_id") or ""))
    return {
        "version": PERSONA_VERSION,
        "traits": traits,
        "constraints": constraints,
    }


def persona_digest(compiled: Optional[Dict]) -> str:
    """Canonical digest of a compiled persona — the determinism assertion.

    `job_card.payload_digest`'s pattern. Criterion 5 ("two compiles produce
    byte-identical constraint sets") becomes one equality on this value, which is
    a far harder thing to pass by accident than comparing two structures field by
    field.
    """
    return hashlib.sha256(
        json.dumps(compiled or {}, sort_keys=True, ensure_ascii=False, default=str)
        .encode("utf-8")
    ).hexdigest()


def leaf_digest(leaves: Sequence[Dict]) -> str:
    """Digest of the leaf set a persona was compiled from — the staleness check.

    Covers `updated_at` as well as identity and status, so a hand-edited leaf
    (which changes text or strength without changing which leaves are active)
    still invalidates the stored persona. Event-driven rebuild is the fast path;
    this is what makes a *missed* rebuild recoverable rather than silent, and a
    persona that silently drops a preference is precisely the failure this tier
    exists to prevent.
    """
    rows = sorted(
        (
            _leaf_id(p),
            _norm(p.get("status") or STATUS_ACTIVE),
            str(p.get("updated_at") or ""),
        )
        for p in leaves or [] if _leaf_id(p)
    )
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


# ── read path ────────────────────────────────────────────────────────────────

def persona_in_scope(
    compiled: Optional[Dict],
    job_id: Optional[str] = None,
    role_family: Optional[str] = None,
) -> List[Dict]:
    """The compiled constraints that bind on this job. Pure.

    Delegates to `preferences.preferences_in_scope` rather than reimplementing
    the rule — the constraint entries deliberately carry the same
    `scope_type`/`scope_value`/`status` fields, so there is exactly one scope
    filter in the codebase and no way for the two tiers to disagree about what
    "in scope" means.
    """
    return preferences_in_scope(
        (compiled or {}).get("constraints") or [],
        job_id=job_id,
        role_family=role_family,
    )


def traits_for(compiled: Optional[Dict], constraints: Sequence[Dict]) -> List[Dict]:
    """The traits covering a scope-filtered constraint set, in trait order.

    Scope filtering happens on leaves, so a trait can be partly in scope; the
    returned trait carries only the `leaf_ids` that survived the filter. A trait
    that lost every leaf is dropped, so the prompt never names a disposition that
    is not binding on this job.
    """
    in_scope: Dict[str, List[str]] = {}
    for entry in constraints or []:
        key = entry.get("trait_key")
        leaf = str(entry.get("preference_id") or "")
        if key and leaf:
            in_scope.setdefault(key, []).append(leaf)
    out: List[Dict] = []
    for trait in (compiled or {}).get("traits") or []:
        leaves = in_scope.get(trait.get("group_key"))
        if leaves:
            out.append({**trait, "leaf_ids": sorted(set(leaves))})
    return out


# ── RL context features (#119) ───────────────────────────────────────────────

def persona_features(constraints: Sequence[Dict], item_key: Any) -> Dict:
    """Per-item persona context for the #119 action schema.

    Defined here and consumed in P3. #119's finding is that strategies are
    sampled per *item* while `context_features` is per *run*, so the predicates
    an induced rule would range over have nothing to bind to. These are the
    persona half of that per-item context: whether a standing preference bound to
    this specific item, and which dispositions it belongs to.

    `trait_keys` rather than trait ids on purpose — the key is stable across
    recomputes and across users, so it works as a context bucket; a row id is
    stable only within one user's database.

    Data only. Nothing here learns, and nothing here changes what is planned.
    """
    key = _norm(item_key)
    features = {
        "has_active_suppress_target": False,
        "has_active_emphasize_target": False,
        "has_active_reframe_target": False,
        "trait_keys": [],
    }
    if not key:
        return features
    keys: List[str] = []
    for entry in constraints or []:
        if _norm(entry.get("target_key")) != key:
            continue
        polarity = _norm(entry.get("polarity")) or PreferencePolarity.SUPPRESS.value
        field = f"has_active_{polarity}_target"
        if field in features:
            features[field] = True
        trait_key = entry.get("trait_key")
        if trait_key:
            keys.append(str(trait_key))
    features["trait_keys"] = sorted(set(keys))
    return features
