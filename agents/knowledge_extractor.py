"""Chain-of-Note knowledge extraction from chat (issue #21).

Turns a chat transcript into *decided* knowledge-artifact proposals: candidate
skills / projects / experiences, each tagged `add`, `supersede`, or `no_op`
against what the user's knowledge graph already holds.

Why two passes. A single "extract new things" prompt conflates two different
judgements — what the conversation claims, and what should change in the graph
— and reliably misses the case that matters most: a later turn *contradicting*
an earlier fact ("I was a Senior Engineer at Acme" … "I got promoted to Staff").
The P0 reading arc (LongMemEval, #108) landed on Chain-of-Note for exactly this:
write grounded notes first, then reason over the notes plus the existing facts.
So:

    extract  →  notes    (what the transcript claims, each with a quote)
    reason   →  decisions (add / supersede / no_op, against known facts)
    decide   →  proposals (deterministic normalization + safety net)

Both LLM passes go through `llm.get_extractor` — the #142 structured-extraction
seam — so output is schema-validated by `with_structured_output` and LangSmith
traces both. There is no `JsonOutputParser` and no `json.loads` here by design.

The third node is *not* an LLM call. It normalizes the model's free-form
decision strings and applies a deterministic equality check that forces `no_op`
when the artifact is already in the profile, so a hallucinated `add` cannot
create a duplicate. The model is only genuinely needed for the contradiction
case.

Composition is a small LangGraph `StateGraph` (`extract → reason → decide`).
Per the framework-adoption decision this is the *one* surface in ARTie that
adopts LangGraph; arbitration and grouping elsewhere stay deterministic. The
nodes are plain module-level functions over a dict state, so every step is
unit-testable on its own and the graph only supplies composition and per-node
trace spans. `run_chain_of_note()` falls back to calling the nodes in sequence
if LangGraph is unavailable, so the pipeline never hard-depends on it.

Nothing in this module writes to the database. Persisting a proposal is an
explicit user action — see `services.apply_artifact_decision`.
"""
import logging
from typing import Any, Dict, List, Optional, TypedDict

from agents.extraction_schemas import (
    ArtifactDecisionList,
    ChatArtifactNoteList,
)
from llm import ModelRole, get_extractor

logger = logging.getLogger(__name__)

ARTIFACT_TYPES = ("skill", "project", "experience")
DECISIONS = ("add", "supersede", "no_op")

# How many recent turns and how much of each to feed the extractor.
_MAX_TURNS = 10
_MAX_CHARS_PER_TURN = 500


# ── transcript / known-fact rendering ─────────────────────────────────────────

def build_transcript(messages: List[Dict]) -> str:
    """Compact `role: content` transcript of the most recent turns."""
    lines = []
    for msg in (messages or [])[-_MAX_TURNS:]:
        role = msg.get("role", "user")
        if role not in ("user", "assistant"):
            continue
        content = (msg.get("content") or "")[:_MAX_CHARS_PER_TURN]
        lines.append(f"{role.capitalize()}: {content}")
    return "\n".join(lines)


def load_known_facts(user_id) -> List[Dict]:
    """The user's current knowledge-graph facts, as flat comparable dicts.

    One dict per skill / project / experience with a `label` the reason prompt
    can quote back as a decision `target`. Read-only; returns [] on any DB
    problem so extraction degrades to "everything looks new" rather than
    failing the chat turn.
    """
    from sqlmodel import Session, select

    from database.db import engine
    from database.models import Experience, Project, Skill, UserSkill

    facts: List[Dict] = []
    try:
        with Session(engine) as session:
            links = session.exec(
                select(UserSkill).where(UserSkill.user_id == user_id)
            ).all()
            for link in links:
                skill = session.get(Skill, link.skill_id)
                if not skill:
                    continue
                facts.append({
                    "type": "skill",
                    "name": skill.name,
                    "category": skill.category,
                    "proficiency": link.proficiency,
                    "label": f"skill: {skill.name}",
                })
            for project in session.exec(
                select(Project).where(Project.user_id == user_id)
            ).all():
                facts.append({
                    "type": "project",
                    "name": project.name,
                    "description": project.description,
                    "label": f"project: {project.name}",
                })
            for exp in session.exec(
                select(Experience).where(Experience.user_id == user_id)
            ).all():
                facts.append({
                    "type": "experience",
                    "name": exp.title,
                    "company": exp.company,
                    "label": f"experience: {exp.title} @ {exp.company}",
                })
    except Exception as exc:
        logger.warning("load_known_facts failed, treating graph as empty: %s", exc)
        return []
    return facts


def _render_facts(known_facts: List[Dict]) -> str:
    if not known_facts:
        return "(the profile is empty — every grounded note is new)"
    return "\n".join(f"- {f['label']}" for f in known_facts)


# ── node 1: extract ───────────────────────────────────────────────────────────

_EXTRACT_INSTRUCTIONS = (
    "You are reading an excerpt of a conversation between a job seeker and their "
    "resume assistant. Write one note per skill, project, or work experience the "
    "USER claims about themselves.\n\n"
    "Rules:\n"
    "- Every note needs an `evidence` quote taken verbatim from the transcript. "
    "If you cannot quote it, do not write the note.\n"
    "- Note things the user states about themselves, including a claim that "
    "revises something they said earlier. Do not note things the assistant "
    "merely suggested, or things the user is asking about rather than claiming.\n"
    "- `type` is exactly one of: skill, project, experience.\n"
    "- For an experience, put the job title in `name` and the employer in `company`.\n"
    "- In `note`, say in one line what the transcript claims — and say so "
    "explicitly when it revises an earlier statement.\n"
    "- Write notes for facts already in the profile too; deciding what is new is "
    "a later step, not yours."
)


def extract_notes(
    transcript: str,
    known_facts: Optional[List[Dict]] = None,
    *,
    extractor=None,
) -> List[Dict]:
    """Transcript → grounded Chain-of-Note notes (list of dicts).

    Notes without an evidence quote are dropped here: an artifact that cannot be
    traced to something the user actually said is not safe to offer for saving.
    Returns [] on extraction failure so a chat turn never dies on this path.
    """
    if not (transcript or "").strip():
        return []
    extractor = extractor or get_extractor(
        role=ModelRole.EXTRACT, schema=ChatArtifactNoteList)
    prompt = (
        f"{_EXTRACT_INSTRUCTIONS}\n\n"
        f"Already in the user's profile:\n{_render_facts(known_facts or [])}\n\n"
        f"Conversation:\n{transcript}"
    )
    try:
        result = extractor.invoke([{"role": "user", "content": prompt}])
    except Exception as exc:
        logger.warning("Chain-of-Note extraction failed: %s", exc)
        return []

    notes: List[Dict] = []
    for note in result.notes:
        item = note.model_dump()
        atype = (item.get("type") or "").strip().lower()
        name = (item.get("name") or "").strip()
        evidence = (item.get("evidence") or "").strip()
        if atype not in ARTIFACT_TYPES or not name or not evidence:
            logger.debug("Dropped note %r (type/name/evidence incomplete)", item)
            continue
        item["type"], item["name"], item["evidence"] = atype, name, evidence
        notes.append(item)
    return notes


# ── node 2: reason ────────────────────────────────────────────────────────────

_REASON_INSTRUCTIONS = (
    "You maintain a job seeker's structured profile. For each numbered note "
    "below, decide what should happen to the profile.\n\n"
    "- `add`       — a genuinely new fact the profile does not hold.\n"
    "- `supersede` — the note contradicts or updates a fact already listed "
    "(a promotion, a corrected proficiency, a renamed or re-scoped project). "
    "Put the existing fact's line in `target`.\n"
    "- `no_op`     — the profile already records this, unchanged. Put the "
    "existing fact's line in `target`.\n\n"
    "Return exactly one decision per note, with `note_index` set to the note's "
    "number. Prefer `supersede` over `add` when the note is about the same "
    "thing as an existing fact — duplicating a fact is worse than updating it."
)


def _render_notes(notes: List[Dict]) -> str:
    lines = []
    for i, note in enumerate(notes):
        parts = [f"{i}. [{note['type']}] {note['name']}"]
        if note.get("company"):
            parts.append(f"at {note['company']}")
        if note.get("category"):
            parts.append(f"({note['category']})")
        line = " ".join(parts)
        if note.get("note"):
            line += f"\n   claim: {note['note']}"
        line += f"\n   evidence: \"{note['evidence']}\""
        lines.append(line)
    return "\n".join(lines)


def reason_decisions(
    notes: List[Dict],
    known_facts: Optional[List[Dict]] = None,
    *,
    extractor=None,
) -> Dict[int, Dict]:
    """Notes + known facts → `{note_index: decision dict}`.

    Returns {} on failure; `decide()` then falls back to its deterministic
    judgement for every note, so a reasoning outage degrades to the pre-#21
    behavior (propose as new unless it already exists) instead of losing the
    turn.
    """
    if not notes:
        return {}
    extractor = extractor or get_extractor(
        role=ModelRole.EXTRACT, schema=ArtifactDecisionList)
    prompt = (
        f"{_REASON_INSTRUCTIONS}\n\n"
        f"Facts already in the profile:\n{_render_facts(known_facts or [])}\n\n"
        f"Notes:\n{_render_notes(notes)}"
    )
    try:
        result = extractor.invoke([{"role": "user", "content": prompt}])
    except Exception as exc:
        logger.warning("Chain-of-Note reasoning failed: %s", exc)
        return {}

    by_index: Dict[int, Dict] = {}
    for decision in result.decisions:
        item = decision.model_dump()
        idx = item.get("note_index")
        if not isinstance(idx, int) or not 0 <= idx < len(notes):
            continue
        by_index[idx] = item
    return by_index


# ── node 3: decide (deterministic) ────────────────────────────────────────────

def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _matching_fact(note: Dict, known_facts: List[Dict]) -> Optional[Dict]:
    """The known fact this note is about, by the simple equality check #21 scopes.

    Deliberately name/company equality only — fuzzy merging of near-miss
    artifacts is explicitly out of scope for this issue.
    """
    for fact in known_facts or []:
        if fact.get("type") != note.get("type"):
            continue
        if _norm(fact.get("name")) != _norm(note.get("name")):
            continue
        if note["type"] == "experience" and _norm(fact.get("company")) != _norm(
                note.get("company")):
            continue
        return fact
    return None


def _fact_by_label(target: Any, known_facts: List[Dict]) -> Optional[Dict]:
    """The known fact whose `label` the reason step quoted back as its target."""
    if not target:
        return None
    wanted = _norm(target)
    for fact in known_facts or []:
        if _norm(fact.get("label")) == wanted:
            return fact
    return None


def _fact_is_unchanged(note: Dict, fact: Dict) -> bool:
    """True when the note adds nothing over the fact already stored.

    Only fields the note actually supplies are compared: a note that omits
    `category` says nothing about the stored category and must not be read as
    contradicting it.
    """
    for field in ("category", "proficiency", "company"):
        new = note.get(field)
        if new in (None, ""):
            continue
        if _norm(new) != _norm(fact.get(field)):
            return False
    return True


def decide(
    notes: List[Dict],
    decisions: Optional[Dict[int, Dict]] = None,
    known_facts: Optional[List[Dict]] = None,
) -> List[Dict]:
    """Fold notes + LLM decisions into normalized, deterministic proposals.

    The two layers split by what each is actually good at.

    Where the note matches an existing fact by plain equality, the deterministic
    rules win outright — the model gets no say:

    - a match the note does not change is a duplicate, so `add` becomes `no_op`;
    - a match the note *does* change is an update, so `add` becomes `supersede`.

    Where there is no equality match, the model's judgement is what's left, and
    it is the case the model exists for: a promotion or a rename changes the very
    name equality would key on ("Senior Engineer" → "Staff Engineer" at the same
    employer). So a `supersede` is honored when its `target` resolves to a real
    fact of the same type. Anything else — an unresolvable target, a `no_op`
    against nothing — falls back to `add`, which is the only coherent action when
    the graph holds nothing to update.
    """
    decisions = decisions or {}
    known_facts = known_facts or []
    proposals: List[Dict] = []

    for i, note in enumerate(notes or []):
        raw = decisions.get(i, {})
        decision = _norm(raw.get("decision")).replace("-", "_").replace(" ", "_")
        if decision not in DECISIONS:
            decision = "add"
        fact = _matching_fact(note, known_facts)
        target_fact = _fact_by_label(raw.get("target"), known_facts)

        if fact is not None:
            target = fact.get("label")
            if _fact_is_unchanged(note, fact):
                decision = "no_op"
            elif decision == "add":
                decision = "supersede"
        elif (decision == "supersede" and target_fact is not None
                and target_fact.get("type") == note.get("type")):
            target = target_fact.get("label")
        else:
            decision, target = "add", None

        proposals.append({
            **note,
            "decision": decision,
            "target": target,
            "rationale": raw.get("rationale"),
        })
    return proposals


# ── the subgraph ──────────────────────────────────────────────────────────────


class CoNState(TypedDict, total=False):
    """State channels of the Chain-of-Note subgraph.

    Declared explicitly rather than as a bare ``dict``: LangGraph merges
    per-declared-channel, so a bare dict schema would have each node's return
    *replace* the whole state and `decide` would never see `notes`.
    """
    transcript: str
    known_facts: List[Dict]
    notes: List[Dict]
    decisions: Dict[int, Dict]
    proposals: List[Dict]


def _nodes(extract_extractor=None, reason_extractor=None):
    """The three node functions, with any injected extractors closed over.

    Extractors are passed here rather than through graph state — they are live
    objects, not data the graph should be carrying or checkpointing.
    """

    def node_extract(state: Dict) -> Dict:
        return {"notes": extract_notes(
            state.get("transcript", ""), state.get("known_facts"),
            extractor=extract_extractor,
        )}

    def node_reason(state: Dict) -> Dict:
        return {"decisions": reason_decisions(
            state.get("notes") or [], state.get("known_facts"),
            extractor=reason_extractor,
        )}

    def node_decide(state: Dict) -> Dict:
        return {"proposals": decide(
            state.get("notes") or [], state.get("decisions"),
            state.get("known_facts"),
        )}

    return node_extract, node_reason, node_decide


def build_graph(extract_extractor=None, reason_extractor=None):
    """The `extract → reason → decide` subgraph, compiled.

    Kept intentionally thin: the nodes just call the plain functions above, so
    the graph adds composition and per-node tracing without owning any logic.
    Raises if LangGraph is unavailable — `run_chain_of_note` handles that.
    """
    from langgraph.graph import END, START, StateGraph

    node_extract, node_reason, node_decide = _nodes(
        extract_extractor, reason_extractor)
    graph = StateGraph(CoNState)
    graph.add_node("extract", node_extract)
    graph.add_node("reason", node_reason)
    graph.add_node("decide", node_decide)
    graph.add_edge(START, "extract")
    graph.add_edge("extract", "reason")
    graph.add_edge("reason", "decide")
    graph.add_edge("decide", END)
    return graph.compile()


def run_chain_of_note(
    messages: List[Dict],
    known_facts: Optional[List[Dict]] = None,
    *,
    extract_extractor=None,
    reason_extractor=None,
) -> List[Dict]:
    """Chat messages → decided artifact proposals. Never raises.

    Each proposal is the note plus `decision` (add/supersede/no_op), the
    `target` fact it refers to, and the model's `rationale`. Writes nothing —
    persisting a proposal is an explicit user action.
    """
    state: Dict = {
        "transcript": build_transcript(messages),
        "known_facts": known_facts or [],
    }
    try:
        return build_graph(extract_extractor, reason_extractor).invoke(
            state).get("proposals") or []
    except Exception as exc:
        # LangGraph missing or a graph-level failure: the nodes are plain
        # functions, so run them in sequence rather than losing the turn.
        logger.warning("Chain-of-Note subgraph unavailable (%s); running linearly", exc)
        try:
            node_extract, node_reason, node_decide = _nodes(
                extract_extractor, reason_extractor)
            state.update(node_extract(state))
            state.update(node_reason(state))
            return node_decide(state).get("proposals") or []
        except Exception as inner:
            logger.warning("Chain-of-Note extraction failed: %s", inner)
            return []
