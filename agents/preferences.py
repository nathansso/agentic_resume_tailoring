"""Standing user preferences, extracted from tailoring chat (issue #129).

The candidate-side mirror of `agents/jd_profile.py`. #121 codified what the
*job* wants as `requirements[].criticality`; this codifies what the *user*
wants as `preferences[].strength` on the same 1-5 scale, and `agents/
arbitration.py` resolves the two against each other at planning time.

**A partial version of this already shipped, and knowing which part matters.**
`agents/job_card.py::extract_rejected_items` already mines the decision log for
items the user dropped, tracks reversal as supersession, tags user-vs-planner
provenance, and `tailor_planner` already pushes those at the model as standing
preferences. What it cannot do is the reason this module exists:

  - it infers from **actions**, not from language — "that was just a class
    project" moves nothing, so it is invisible to a log of edit ops;
  - it has one polarity (dropped), no strength, and no scope beyond one job;
  - it reaches the planner as **prose in a prompt**, which ImplexConv finding 2
    measures directly: retrieval of the invalidating fact succeeds and the model
    still answers wrong, because the failure is reasoning, not memory.

So the preference has to gate the action space, not decorate the prompt. That
gate is `tailor_planner.apply_constraints`; this module only produces the typed
preferences it runs on.

**One LLM pass, not Chain-of-Note's two.** #21 needs a reason pass because a
rename changes the very name its equality check keys on ("Senior Engineer" →
"Staff Engineer"), so only a model can tell an update from an addition. That
failure has no analogue here: a preference's identity is its *target*, not its
own wording, and the target is a knowledge-graph key the extraction quotes back
from a supplied list. Two preferences about `proj:recipe-app` are about the same
thing however differently they are phrased, so `resolve_against_existing` is
deterministic and the second model call would buy nothing.

Nothing here writes to the database, matching `jd_profile.py`. Persistence and
the propose/decide split live in `services`, and the write path is only ever
reached by an explicit user action — see the write-barrier note there.
"""
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from agents.extraction_schemas import (
    PreferenceNoteList,
    PreferencePolarity,
    PreferenceScopeType,
    PreferenceTargetType,
)
from llm import ModelRole, get_extractor

logger = logging.getLogger(__name__)

# Bump when the extraction prompt or the compiled preference shape changes.
EXTRACTION_VERSION = 1

POLARITIES = tuple(p.value for p in PreferencePolarity)
TARGET_TYPES = tuple(t.value for t in PreferenceTargetType)
SCOPE_TYPES = tuple(s.value for s in PreferenceScopeType)

STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_RETRACTED = "retracted"

_STRENGTH_MIN, _STRENGTH_MAX, _STRENGTH_DEFAULT = 1, 5, 3

# Sections a preference may name. Matches tailor.REORDERABLE_SECTIONS; kept as a
# literal rather than imported to keep this module free of the tailoring import
# graph (agents.tailor imports this one).
SECTION_NAMES = ("education", "experience", "projects", "skills", "achievements")

# How many recent turns feed the extractor, matching knowledge_extractor. The
# active job chat is never compressed (#109), so extraction reads real turns
# rather than a summary and no preference is lost before it is captured.
_MAX_TURNS = 12
_MAX_CHARS_PER_TURN = 600

_WORD_RE = re.compile(r"[a-z0-9+#.]+")


# ── normalization ────────────────────────────────────────────────────────────

def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _clamp_strength(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return _STRENGTH_DEFAULT
    return max(_STRENGTH_MIN, min(_STRENGTH_MAX, n))


def _clamp_confidence(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, min(1.0, f)), 3)


def terms_of(text: Any) -> List[str]:
    """Comparable lowercase terms from a phrase.

    The unit arbitration matches on. Lowercased to line up with
    `jd_profile._clean_terms`, which is what these are compared against.
    """
    return _WORD_RE.findall(_norm(text))


# ── target catalog ───────────────────────────────────────────────────────────

def target_catalog(user_id) -> List[Dict]:
    """The things a preference may be about: the user's KG items plus sections.

    Each entry is `{key, label, target_type}`. `key` uses the same normalized
    forms `agents/tailor.py` builds its planner item keys from (`exp:<title>|
    <company>`, `proj:<name>`), which is what lets an accepted preference bind
    directly onto a planner action instead of being re-matched by prose later.

    Built on `knowledge_extractor.load_known_facts` so there is exactly one
    knowledge-graph reader on the chat path; that helper already degrades to []
    on a DB problem rather than failing the turn.
    """
    from agents.knowledge_extractor import load_known_facts

    catalog: List[Dict] = []
    for fact in load_known_facts(user_id):
        ftype = fact.get("type")
        name = _norm(fact.get("name"))
        if not name:
            continue
        if ftype == "experience":
            key = f"exp:{name}|{_norm(fact.get('company'))}"
        elif ftype == "project":
            key = f"proj:{name}"
        elif ftype == "skill":
            key = f"skill:{name}"
        else:
            continue
        catalog.append({
            "key": key,
            "label": fact.get("label") or name,
            "target_type": ftype,
        })
    for section in SECTION_NAMES:
        catalog.append({
            "key": f"section:{section}",
            "label": f"section: {section}",
            "target_type": "section",
        })
    return catalog


def _render_catalog(catalog: Sequence[Dict]) -> str:
    if not catalog:
        return "(no items on file — every preference is about a bare topic)"
    return "\n".join(f"- {c['label']}" for c in catalog)


def _match_target(label: Any, catalog: Sequence[Dict]) -> Optional[Dict]:
    """The catalog entry the extraction's `target_label` refers to.

    Exact label match first (the model was asked to quote one back), then the
    bare name after the `type: ` prefix, then a containment check so "the recipe
    app" still resolves to "project: Recipe App". Deliberately no embedding
    similarity: ImplexConv finding 4 is that similarity retrieval is exactly
    what fails on this data, and a wrong bind here silently suppresses the wrong
    resume item.
    """
    wanted = _norm(label)
    if not wanted:
        return None
    for entry in catalog:
        if _norm(entry["label"]) == wanted:
            return entry
    for entry in catalog:
        bare = _norm(entry["label"].split(":", 1)[-1])
        if bare and bare == wanted:
            return entry
    for entry in catalog:
        bare = _norm(entry["label"].split(":", 1)[-1])
        if bare and (bare in wanted or wanted in bare):
            return entry
    return None


# ── extraction ───────────────────────────────────────────────────────────────

def build_transcript(messages: Sequence[Dict]) -> str:
    """Compact `role: content` transcript of the most recent turns."""
    lines = []
    for msg in list(messages or [])[-_MAX_TURNS:]:
        role = msg.get("role", "user")
        if role not in ("user", "assistant"):
            continue
        content = (msg.get("content") or "")[:_MAX_CHARS_PER_TURN]
        lines.append(f"{role.capitalize()}: {content}")
    return "\n".join(lines)


_EXTRACT_INSTRUCTIONS = (
    "You are reading a conversation between a job seeker and their resume "
    "assistant. Write one note per standing PREFERENCE the USER expresses about "
    "how their resume should be built.\n\n"
    "A preference is a constraint on selection or framing — what to leave out, "
    "what to lead with, how to present something. It is not a fact about the "
    "candidate: 'I built a Redis cache at Acme' is a fact and belongs in their "
    "profile, not here. 'Don't lead with the Acme job' is a preference.\n\n"
    "Rules:\n"
    "- Only the USER's preferences. Never record something the assistant "
    "suggested and the user did not endorse.\n"
    "- Every note needs an `evidence` quote taken verbatim from the "
    "conversation. Omit the note rather than inventing a quote.\n"
    "- Write `text` as a standalone instruction, understandable by someone who "
    "cannot see this conversation.\n"
    "- When the preference names one of the candidate's known items listed "
    "below, quote that item's line back as `target_label`.\n"
    "- Most preferences are about leaving something out or playing it down — "
    "record those as polarity 'suppress'. Do not soften a negative preference "
    "into a positive one about something else.\n"
    "- Prefer the narrowest `scope_type` that fits what the user said. Only use "
    "'global' for a rule about every resume they will ever send.\n"
    "- Reserve strength 5 for absolutes. A 5 is honored even when the job "
    "explicitly asks for the opposite, so it must reflect the user's own "
    "insistence and not your judgement of what matters.\n"
    "- Record nothing when the conversation states no preferences. An empty "
    "list is the correct answer far more often than not."
)


def extract_preference_notes(
    transcript: str,
    catalog: Optional[Sequence[Dict]] = None,
    *,
    extractor=None,
) -> List[Dict]:
    """Transcript → grounded preference notes (list of dicts).

    The single LLM call, through the #142 `get_extractor` seam. Notes without an
    evidence quote or a preference statement are dropped, mirroring #21: a
    preference that cannot be traced to something the user actually said is not
    safe to offer, and this tier suppresses resume content when honored.

    Returns [] on any failure so a chat turn never dies on this path.
    """
    if not (transcript or "").strip():
        return []
    extractor = extractor or get_extractor(
        role=ModelRole.EXTRACT, schema=PreferenceNoteList)
    prompt = (
        f"{_EXTRACT_INSTRUCTIONS}\n\n"
        f"The candidate's known items:\n{_render_catalog(catalog or [])}\n\n"
        f"Conversation:\n{transcript}"
    )
    try:
        result = extractor.invoke([{"role": "user", "content": prompt}])
    except Exception as exc:
        logger.warning("Preference extraction failed: %s", exc)
        return []

    notes: List[Dict] = []
    for note in result.notes:
        item = note.model_dump()
        text = (item.get("text") or "").strip()
        evidence = (item.get("evidence") or "").strip()
        if not text or not evidence:
            logger.debug("Dropped preference note %r (text/evidence missing)", item)
            continue
        item["text"], item["evidence"] = text, evidence
        notes.append(item)
    return notes


# ── deterministic compile ────────────────────────────────────────────────────

def _enum_value(value: Any, allowed: Sequence[str], default: str) -> str:
    raw = value.value if hasattr(value, "value") else value
    normalized = _norm(raw).replace("-", "_").replace(" ", "_")
    return normalized if normalized in allowed else default


def compile_preferences(
    notes: Sequence[Dict],
    catalog: Optional[Sequence[Dict]] = None,
    provenance: Optional[Dict] = None,
) -> List[Dict]:
    """Normalize extracted notes into stored-preference shape. Pure.

    Everything past the model call happens here, so the same notes always
    compile to the same preferences — the property that makes the arbitration
    downstream deterministic rather than a function of model temperature.

    Targets are resolved to knowledge-graph keys where they match. A target that
    resolves to nothing is **kept**, as a `topic` with only its term: the user
    said it, and "don't mention my current employer" is a real preference about
    something that is deliberately not an item on the resume. Arbitration
    handles an unbindable target by term-matching rather than by item key.
    """
    catalog = list(catalog or [])
    base_provenance = dict(provenance or {})
    out: List[Dict] = []
    seen: set = set()

    for note in notes or []:
        text = (note.get("text") or "").strip()
        if not text:
            continue
        polarity = _enum_value(
            note.get("polarity"), POLARITIES, PreferencePolarity.SUPPRESS.value)
        target_type = _enum_value(
            note.get("target_type"), TARGET_TYPES, PreferenceTargetType.TOPIC.value)
        scope_type = _enum_value(
            note.get("scope_type"), SCOPE_TYPES, PreferenceScopeType.JOB.value)

        label = (note.get("target_label") or "").strip()
        match = _match_target(label, catalog)
        target_key = match["key"] if match else None
        if match:
            target_type = match["target_type"]

        scope_value = (note.get("scope_value") or "").strip() or None
        if scope_type == PreferenceScopeType.JOB.value:
            scope_value = base_provenance.get("job_id") or scope_value
        elif scope_type == PreferenceScopeType.GLOBAL.value:
            scope_value = None
        elif not scope_value:
            # A role_family scope with no family named cannot be filtered
            # against a future job, so it would silently behave as global.
            # Narrow it to this job instead of widening it by accident.
            scope_type = PreferenceScopeType.JOB.value
            scope_value = base_provenance.get("job_id")

        # Identity for dedupe within one extraction: the same target and
        # polarity said twice in a conversation is one preference.
        identity = (polarity, target_key or _norm(label), scope_type, str(scope_value or ""))
        if identity in seen:
            continue
        seen.add(identity)

        out.append({
            "text": text,
            "polarity": polarity,
            "target_type": target_type,
            "target_key": target_key,
            "target_term": label or None,
            "scope_type": scope_type,
            "scope_value": str(scope_value) if scope_value else None,
            "strength": _clamp_strength(note.get("strength")),
            "confidence": _clamp_confidence(note.get("confidence")),
            "status": STATUS_ACTIVE,
            "extraction_version": EXTRACTION_VERSION,
            "provenance": {
                **base_provenance,
                "quote": (note.get("evidence") or "").strip(),
                "extracted_at": base_provenance.get(
                    "extracted_at", datetime.utcnow().isoformat()),
            },
        })
    return out


# ── supersession ─────────────────────────────────────────────────────────────

def _same_subject(a: Dict, b: Dict) -> bool:
    """Whether two preferences are about the same thing.

    Key equality when both bound to the knowledge graph; otherwise term
    equality on the stated subject. Never text equality on the preference
    statement itself — "skip the recipe app" and "don't include that cooking
    project" are the same preference worded differently, and treating them as
    distinct is how a profile accumulates duplicates that all fire at once.
    """
    if a.get("target_key") and b.get("target_key"):
        return a["target_key"] == b["target_key"]
    if a.get("target_key") or b.get("target_key"):
        return False
    return _norm(a.get("target_term")) == _norm(b.get("target_term"))


def _same_scope(a: Dict, b: Dict) -> bool:
    return (a.get("scope_type") == b.get("scope_type")
            and _norm(a.get("scope_value")) == _norm(b.get("scope_value")))


def resolve_against_existing(
    compiled: Sequence[Dict], existing: Sequence[Dict],
) -> List[Dict]:
    """Tag each compiled preference `add`, `supersede`, or `no_op`. Pure.

    Deterministic, for the reason in the module docstring: a preference's
    identity is its target, which the extraction already resolved, so there is
    no rename case a model would be needed for.

    - Same subject, same scope, **same** polarity → `no_op`. Already held.
    - Same subject, same scope, **different** polarity → `supersede`. This is
      the reversal case ("actually, put the ML back") and it is the one that
      must not be lossy: the superseded row stays on the table with
      `status='superseded'`, because a contradicted preference is superseded and
      never deleted (#133).
    - Otherwise → `add`.

    A hand-edited existing preference is still superseded by an explicit later
    reversal — `edited` protects a correction from being *overwritten by a
    re-extraction*, not from the user changing their mind out loud.
    """
    active = [
        p for p in existing or []
        if (p.get("status") or STATUS_ACTIVE) == STATUS_ACTIVE
    ]
    out: List[Dict] = []
    for pref in compiled or []:
        decision, target = "add", None
        for prior in active:
            if not (_same_subject(pref, prior) and _same_scope(pref, prior)):
                continue
            if prior.get("polarity") == pref.get("polarity"):
                decision, target = "no_op", prior
            else:
                decision, target = "supersede", prior
            break
        entry = {**pref, "decision": decision}
        if target is not None:
            entry["supersedes_id"] = target.get("preference_id")
            entry["supersedes_text"] = target.get("text")
        out.append(entry)
    return out


# ── scope filter ─────────────────────────────────────────────────────────────

def preferences_in_scope(
    preferences: Sequence[Dict],
    job_id: Optional[str] = None,
    role_family: Optional[str] = None,
) -> List[Dict]:
    """The active preferences that bind on this job. Pure.

    The push side of the KG/persona split (#109): the planner is handed this
    scope-filtered set directly rather than searching for it, because the
    dominant signal here is negation and a suppression is semantically distant
    from what it suppresses — similarity search structurally misses exactly the
    preferences that matter most.

    Sorted by `(preference_id)` so the compiled constraint set is stable
    regardless of row order coming back from the database.
    """
    job = _norm(job_id)
    family = _norm(role_family)
    out: List[Dict] = []
    for pref in preferences or []:
        if (pref.get("status") or STATUS_ACTIVE) != STATUS_ACTIVE:
            continue
        scope = pref.get("scope_type") or PreferenceScopeType.JOB.value
        value = _norm(pref.get("scope_value"))
        if scope == PreferenceScopeType.GLOBAL.value:
            out.append(pref)
        elif scope == PreferenceScopeType.ROLE_FAMILY.value:
            if family and value == family:
                out.append(pref)
        elif job and value == job:
            out.append(pref)
    out.sort(key=lambda p: str(p.get("preference_id") or ""))
    return out
