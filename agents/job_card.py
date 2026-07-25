"""JobCard — distilled memory of a completed job (issue #137).

ARTie's job chats are bounded by the re-tailor cap, so within-session context
management is a non-problem. The axis that scales is **jobs-per-user**: someone
who has tailored to twenty roles has told us, across those sessions, which
projects they lead with and which framings they rejected — and none of it
survived into the next job. A JobCard is the sufficient statistic that does.

Three properties define this module:

1. **The card is a projection, not a summary.** `compile_card_payload` reads
   `UserJobResult` and the job's final state and rearranges them. No LLM ever
   sees the transcript, so two compiles of the same result are byte-identical
   (`payload_hash`). The *one* exception is `role_family`, a cached, versioned
   classify through the #142 `get_extractor` seam — cached precisely so a
   rebuild is not an LLM call.

2. **The negation signal is the point.** `rejected_items` carries what the user
   took *out*. That is the field a generic recap drops, and it is exactly where
   summarization silently fails (#129 finding 1: opposed-case retrieval F1 is
   14.8%). Rejections are therefore *pushed* with the card, never left to be
   retrieved — and `render_cards` will not drop a user-sourced rejection to fit
   a budget. For the same reason they are deliberately **not** added to
   `index_keys`: similarity search cannot represent "not this".

3. **Selection is bounded and multi-key.** `select_cards` ranks on role-family
   match + JD-embedding similarity + fact-level key overlap + recency
   (LongMemEval #108, findings 1 and 2), then `render_cards` caps the result at
   a token budget — so prompt cost does not grow with the number of jobs.

Embedding similarity goes through the #142 vector seam in **candidates mode**:
card vectors are held in memory and passed in. The `session=`/`model_cls=`
table-scan mode is Postgres-only and returns `[]` on SQLite, which would zero
out injection across local dev and the whole test suite while the tests still
passed. Card counts are tens per user, so numpy over that is free and ANN buys
nothing.

Nothing here writes to the database — persistence and the event-driven rebuild
live in `services.rebuild_job_card`, mirroring how
`agents/knowledge_extractor.py` stays pure against
`services.apply_artifact_decision`.
"""
import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agents.extraction_schemas import RoleFamily, RoleFamilyClassification
from agents.skill_scorer import _env_float, _env_int
from database.vector_search import search_similar
from llm import ModelRole, get_extractor

logger = logging.getLogger(__name__)

# Payload schema version. Bump when the compile's *shape* changes so a stale
# card is recompiled rather than silently read with the wrong keys.
CARD_VERSION = 1

# Classification version: bump when the label set or the classify prompt
# changes, which invalidates every cached role_family.
ROLE_FAMILY_VERSION = 1

# Whether a recorded rejection still binds. A reversed rejection is kept, never
# deleted (#133: "negation must not expire") — it is history the policy work
# (#51 Phase 2 / #119) needs, but it must not be pushed at the planner as a
# current preference.
REJECTION_ACTIVE = "active"
REJECTION_REVERSED = "reversed"

# ATS components carried on the card, matching the set
# `tailor_planner.decision_log_entry` records so card and decision log stay
# directly comparable.
_ATS_COMPONENTS = ("skill_coverage", "keyword_coverage", "section_presence", "role_level")

# Per-card list caps. The card is bounded by construction, so the token budget
# in render_cards only has to decide *how many* cards fit — never how much of
# one. User-sourced rejections are exempt (see _render_card).
_MAX_ITEMS = 6
_MAX_SKILLS = 20


def top_n() -> int:
    """How many cards may reach the prompt at most."""
    return max(1, _env_int("JOBCARD_TOP_N", 3))


def token_budget() -> int:
    """Upper bound on the rendered card block, in estimated tokens."""
    return max(1, _env_int("JOBCARD_TOKEN_BUDGET", 600))


def _weights() -> Dict[str, float]:
    """Selection weights. Env-tunable, but *fixed* — learning them from outcomes
    is #51 Phase 2 / #119 consuming this metric, explicitly not this issue."""
    return {
        "role": _env_float("JOBCARD_W_ROLE", 0.35),
        "embed": _env_float("JOBCARD_W_EMBED", 0.35),
        "keys": _env_float("JOBCARD_W_KEYS", 0.20),
        "recency": _env_float("JOBCARD_W_RECENCY", 0.10),
    }


# ── normalization helpers ─────────────────────────────────────────────────────

def _norm(value: Any) -> str:
    """Lowercase, punctuation to spaces, whitespace collapsed."""
    s = re.sub(r"[^a-z0-9 ]+", " ", str(value or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _as_obj(value, default):
    """Normalise a JSON column that may round-trip as a JSON string on SQLite.

    Same defensive coercion `agents/tailor.py::_as_obj` applies for the same
    reason — a card compiled from a string-typed column would be empty.
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return default
    return value if value is not None else default


def _iso(value) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def _dedupe(values: Sequence[str], limit: Optional[int] = None) -> List[str]:
    """Order-preserving case-insensitive dedupe, optionally capped."""
    seen: set = set()
    out: List[str] = []
    for value in values:
        text = str(value or "").strip()
        key = _norm(text)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
        if limit is not None and len(out) >= limit:
            break
    return out


# ── the one LLM call: cached, versioned role_family classify ──────────────────

_CLASSIFY_INSTRUCTIONS = (
    "Classify the role this job description is hiring for into exactly one "
    "family from the allowed set. Judge the role's actual day-to-day work, not "
    "the company's industry. Use 'other' only when the role genuinely fits none "
    "of the families."
)


def role_family_key(title: str, company: str, description: str) -> str:
    """Digest of the classify inputs.

    The cache key: a stored `role_family` is reused unless this digest or
    ROLE_FAMILY_VERSION changes, which is what keeps a card rebuild free of LLM
    calls. Only the leading description text is hashed — the same slice the
    classify prompt sees — so an edit the classifier could not have noticed does
    not force a reclassification.
    """
    payload = f"{_norm(title)}|{_norm(company)}|{_norm(description)[:2000]}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def classify_role_family(
    title: str,
    company: str = "",
    description: str = "",
    *,
    extractor=None,
) -> str:
    """Return one `RoleFamily` value for this job.

    The single LLM call this issue is allowed, routed through the #142
    structured-extraction seam so the label is enum-validated by
    `with_structured_output` rather than coerced from free text. Degrades to
    `"other"` on any failure — a classification outage must not fail a card
    compile, which in turn must not fail a tailoring run.

    *extractor* is injectable so tests script the seam directly (the
    `tests/test_knowledge_extraction.py::_Scripted` pattern).
    """
    if not (title or description or "").strip():
        return RoleFamily.OTHER.value
    extractor = extractor or get_extractor(
        role=ModelRole.EXTRACT, schema=RoleFamilyClassification)
    prompt = (
        f"{_CLASSIFY_INSTRUCTIONS}\n\n"
        f"Allowed families: {', '.join(f.value for f in RoleFamily)}\n\n"
        f"Job title: {title}\n"
        f"Company: {company or '(unspecified)'}\n\n"
        f"Job description:\n{(description or '')[:2000]}"
    )
    try:
        result = extractor.invoke([{"role": "user", "content": prompt}])
    except Exception as exc:
        logger.warning("role_family classification failed, using 'other': %s", exc)
        return RoleFamily.OTHER.value
    family = getattr(result, "role_family", None)
    return family.value if isinstance(family, RoleFamily) else RoleFamily.OTHER.value


# ── deterministic compile ─────────────────────────────────────────────────────

def _ats_summary(result) -> Dict:
    """Composite + per-component breakdown of the shipped tailored output.

    Prefers `tailored_score_breakdown` (the tailored output's own ATS
    breakdown); falls back to the pre-tailor `score_breakdown`, then to the bare
    `ats_score`, so a card compiles for every result shape the DB may hold.
    """
    tailored = _as_obj(getattr(result, "tailored_score_breakdown", None), {}) or {}
    baseline = _as_obj(getattr(result, "score_breakdown", None), {}) or {}
    source = tailored or baseline

    components: Dict[str, float] = {}
    for name in _ATS_COMPONENTS:
        comp = source.get(name)
        if isinstance(comp, dict) and isinstance(comp.get("score"), (int, float)):
            components[name] = round(float(comp["score"]), 3)

    composite = source.get("composite")
    if not isinstance(composite, (int, float)):
        raw = getattr(result, "ats_score", None)
        composite = float(raw) if isinstance(raw, (int, float)) else None

    out: Dict = {
        "composite": round(float(composite), 3) if composite is not None else None,
        "components": components,
    }
    for field in ("baseline_composite", "delta"):
        value = source.get(field)
        if isinstance(value, (int, float)):
            out[field] = round(float(value), 3)
    return out


def _emphasized(result) -> Dict:
    """What the finished resume actually led with.

    `tailored_resume_content` is already in render order — experiences are
    relevance-ranked and projects are score-ordered by the time they are stored
    — so position carries meaning and the first of each section is what the
    resume leads with.
    """
    content = _as_obj(getattr(result, "tailored_resume_content", None), {}) or {}
    if not isinstance(content, dict) or "error" in content:
        content = {}

    experiences = _dedupe(
        [
            " @ ".join(p for p in (e.get("title"), e.get("company")) if p)
            for e in content.get("experiences") or []
            if isinstance(e, dict)
        ],
        _MAX_ITEMS,
    )
    projects = _dedupe(
        [p.get("name") for p in content.get("projects") or [] if isinstance(p, dict)],
        _MAX_ITEMS,
    )

    skill_names: List[str] = [
        s for s in content.get("skills_emphasized") or [] if isinstance(s, str)
    ]
    skill_names += [
        s.get("name") for s in content.get("skills_ranked") or []
        if isinstance(s, dict) and s.get("name")
    ]
    explain = (_as_obj(getattr(result, "matched_skills", None), {}) or {}).get(
        "_explainability") or {}
    if isinstance(explain, dict):
        skill_names += [s for s in explain.get("emphasized") or [] if isinstance(s, str)]

    return {
        "experiences": experiences,
        "projects": projects,
        "skills": _dedupe(skill_names, _MAX_SKILLS),
        "led_experience": experiences[0] if experiences else None,
        "led_project": projects[0] if projects else None,
    }


def _entry_is_user_driven(entry: Dict) -> bool:
    """Whether the human, not the planner, drove this run's drops.

    Two signals, both recorded on the decision-log entry by
    `tailor_planner.decision_log_entry`: a `chat_approved` plan is one a human
    picked in chat, and non-empty `revision_notes` means the user asked for the
    change in words. Anything else is the planner's own call, which is a much
    weaker preference signal and is labelled as such rather than conflated.
    """
    if (entry.get("planner") or "") == "chat_approved":
        return True
    return bool((entry.get("revision_notes") or "").strip())


def extract_rejected_items(tailoring_decisions) -> List[Dict]:
    """Every item ever dropped from the resume, with whether that still stands.

    The negation signal. Reads the append-only decision log (issues #91/#51).

    **Supersession, not deletion.** An item deleted on run 1 and kept on run 2 is
    no longer a *standing* rejection — pushing it at the planner would suppress
    something the user deliberately restored. But the rejection still happened,
    and #133 locks the rule that "a contradicted preference is superseded, never
    deleted — negation must not expire". So the reversal is recorded as
    ``status="reversed"`` rather than dropped: `render_cards` pushes only
    ``active`` rejections at the planner, while the card keeps the full
    rejection history for the #51/#119 policy work to condition on. Dropping it
    would make the card a lossy view of a trajectory that the decision log
    itself retains.

    Ordering is active first, then user-sourced, then most recent, then item key
    — stable, and it puts the rejections that actually bind at the front of any
    truncation.
    """
    log = _as_obj(tailoring_decisions, []) or []
    if not isinstance(log, list):
        return []

    history: Dict[str, List[Dict]] = {}
    for run_index, entry in enumerate(log):
        if not isinstance(entry, dict):
            continue
        user_driven = _entry_is_user_driven(entry)
        for action in entry.get("actions") or []:
            if not isinstance(action, dict):
                continue
            key = str(action.get("item_key") or "").strip()
            if not key:
                continue
            history.setdefault(key, []).append({
                "item_key": key,
                "label": action.get("label") or key,
                "section": action.get("section"),
                "op": (action.get("op") or "").strip().lower(),
                "source": "user" if user_driven else "planner",
                "rationale": str(action.get("rationale") or "").strip()[:200],
                "run": run_index,
            })

    rejected: List[Dict] = []
    for records in history.values():
        rejections = [r for r in records if r["op"] in ("delete", "replace")]
        if not rejections:
            continue
        # Attribute the rejection to the run that made it; judge whether it
        # still stands by the item's most recent action of any kind.
        entry = dict(rejections[-1])
        final = records[-1]
        if final["op"] in ("delete", "replace"):
            entry["status"] = REJECTION_ACTIVE
        else:
            entry["status"] = REJECTION_REVERSED
            entry["reversed_at_run"] = final["run"]
            entry["reversed_by_op"] = final["op"]
        rejected.append(entry)

    rejected.sort(key=lambda r: (
        r["status"] != REJECTION_ACTIVE, r["source"] != "user",
        -r["run"], r["item_key"],
    ))
    return rejected


def active_rejections(payload: Dict, source: Optional[str] = None) -> List[Dict]:
    """Rejections that still stand — what may be pushed at the planner.

    Reads `status` defensively so a card persisted before supersession existed
    (no `status` key) is treated as active, matching its behavior at the time it
    was written.
    """
    rows = [
        r for r in payload.get("rejected_items") or []
        if r.get("status", REJECTION_ACTIVE) == REJECTION_ACTIVE
    ]
    if source is not None:
        rows = [r for r in rows if (r.get("source") == "user") == (source == "user")]
    return rows


def _final_user_score(tailoring_decisions) -> Optional[int]:
    """The most recent 1-5 score the user gave.

    Not a column: the chat score prompt writes it to
    `tailoring_decisions[-1]["reward"]["user_score"]` (`agents/chat.py`). Runs
    the user never scored simply have no key, so we scan backwards for the last
    one that does.
    """
    log = _as_obj(tailoring_decisions, []) or []
    if not isinstance(log, list):
        return None
    for entry in reversed(log):
        if not isinstance(entry, dict):
            continue
        score = (entry.get("reward") or {}).get("user_score")
        if isinstance(score, (int, float)):
            return int(score)
    return None


def compile_card_payload(job, result, role_family: Optional[str] = None) -> Dict:
    """`UserJobResult` + job state → the typed card payload.

    Fully deterministic: given the same rows and the same *role_family*, the
    returned dict is identical, which is what `payload_digest` pins. *job* and
    *result* are duck-typed (ORM rows in production, simple namespaces in tests).
    """
    return {
        "version": CARD_VERSION,
        "job": {
            "title": getattr(job, "title", "") or "",
            "company": getattr(job, "company", "") or "",
            "terminal_status": getattr(job, "status", None),
            "verification_status": getattr(result, "verification_status", None),
        },
        "role_family": role_family,
        "ats": _ats_summary(result),
        "emphasized": _emphasized(result),
        "rejected_items": extract_rejected_items(
            getattr(result, "tailoring_decisions", None)),
        "user_score": _final_user_score(getattr(result, "tailoring_decisions", None)),
        "runs": len(_as_obj(getattr(result, "tailoring_decisions", None), []) or []),
        "timestamps": {
            "created_at": _iso(getattr(result, "created_at", None)),
            "updated_at": _iso(getattr(result, "updated_at", None)),
        },
    }


def payload_digest(payload: Dict) -> str:
    """Canonical digest of a card payload — the determinism assertion."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        .encode("utf-8")
    ).hexdigest()


def build_index_keys(payload: Dict) -> List[str]:
    """Fact-level multi-key index for this card (LongMemEval #108, finding 1).

    A card is indexed under its emphasized items, its skills, and its
    role_family — not under one embedding of the whole card. The expansion
    happens here, at compile time (document-expansion-at-index-time beat
    post-retrieval rank merging in the paper), and it is deterministic because
    the card is.

    Rejected items are deliberately absent. They are a *negation*, and a
    similarity index cannot represent "not this" (#129 finding 1); they ride on
    the card and are pushed with it instead.
    """
    keys: List[str] = []
    family = payload.get("role_family")
    if family:
        keys.append(f"role:{_norm(family)}")
    emphasized = payload.get("emphasized") or {}
    for label in emphasized.get("experiences") or []:
        keys.append(f"exp:{_norm(label)}")
    for label in emphasized.get("projects") or []:
        keys.append(f"proj:{_norm(label)}")
    for name in emphasized.get("skills") or []:
        keys.append(f"skill:{_norm(name)}")
    return sorted({k for k in keys if not k.endswith(":")})


def query_index_keys(
    jd_skill_names: Sequence[str] = (), role_family: Optional[str] = None
) -> List[str]:
    """The query side of the same key space — what the active JD indexes under."""
    keys = [f"skill:{_norm(n)}" for n in jd_skill_names or [] if _norm(n)]
    if role_family:
        keys.append(f"role:{_norm(role_family)}")
    return sorted(set(keys))


# ── relevance-ranked selection ────────────────────────────────────────────────

def _recency_weight(source_updated_at, now: datetime) -> float:
    """Exponential decay on card age (LongMemEval #108, finding 2).

    Cards already carry timestamps, so selection is recency-weighted rather than
    relevance-only: the most recent tailoring of a similar role wins ties.
    """
    if not isinstance(source_updated_at, datetime):
        return 0.0
    half_life = max(1.0, _env_float("JOBCARD_RECENCY_HALFLIFE_DAYS", 90.0))
    age_days = max(0.0, (now - source_updated_at).total_seconds() / 86400.0)
    return float(0.5 ** (age_days / half_life))


def _similarities(
    cards: Sequence[Dict], jd_vector
) -> Dict[Any, float]:
    """card_id → JD similarity, through the #142 vector seam.

    Candidates mode by construction, and **this stays correct on an all-Postgres
    stack** — it is not a SQLite concession. Three reasons, in order of weight:

    1. **The ranking is a four-signal blend** (role match + similarity + key
       overlap + recency, see `select_cards`). `ORDER BY embedding_vec <=> q
       LIMIT n` can only order by *one* of those four terms, so it returns the
       wrong top-N. Using pgvector here would mean over-fetching and re-ranking
       in Python anyway — and at tens of cards per user the over-fetch is the
       entire table, i.e. this function with extra round trips.
    2. `embedding_vec` is unpopulated (no write path — see the follow-up issue),
       so the table-scan mode returns `[]` on Postgres too, today.
    3. Cardinality. 384 dims × tens of rows is one small matmul; ANN indexes
       start paying somewhere around 10^5 vectors.

    The table-scan mode is the right call for a large single-signal search over a
    shared table; it is the wrong call for a small blended per-user ranking.
    """
    candidates: List[Tuple[Any, Any]] = [
        (c.get("card_id"), c.get("embedding"))
        for c in cards
        if c.get("embedding") is not None
    ]
    if jd_vector is None or not candidates:
        return {}
    ranked = search_similar(jd_vector, k=len(candidates), candidates=candidates)
    # Cosine below zero is not weak evidence, it is no evidence — clamp rather
    # than let an unrelated card push a relevant one down the list.
    return {card_id: max(0.0, score) for card_id, score in ranked}


def select_cards(
    cards: Sequence[Dict],
    *,
    jd_vector=None,
    jd_skill_names: Sequence[str] = (),
    role_family: Optional[str] = None,
    now: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> List[Dict]:
    """Rank cards against the active JD and return the top-N.

    *cards* are dicts: `card_id`, `payload`, `index_keys`, `role_family`,
    `embedding` (the *source job's* cached JD centroid), `source_updated_at`.

    Four signals — role-family match, JD-embedding similarity, fact-level key
    overlap, and recency — blended by `_weights()`. Ranking stays meaningful
    when embeddings are unavailable (the other three still separate the cards),
    which is what lets the SQLite suite exercise real ordering rather than a
    degenerate all-zero one.
    """
    if not cards:
        return []
    now = now or datetime.utcnow()
    weights = _weights()
    sims = _similarities(cards, jd_vector)
    query_keys = set(query_index_keys(jd_skill_names, role_family))

    scored: List[Dict] = []
    for card in cards:
        card_keys = set(card.get("index_keys") or [])
        overlap = (
            len(query_keys & card_keys) / len(query_keys) if query_keys else 0.0
        )
        # 'other' is the null label, not a family — two unclassifiable jobs are
        # not evidence of similarity.
        card_family = card.get("role_family")
        role_match = float(
            bool(role_family)
            and card_family == role_family
            and role_family != RoleFamily.OTHER.value
        )
        embed = sims.get(card.get("card_id"), 0.0)
        recency = _recency_weight(card.get("source_updated_at"), now)
        score = (
            weights["role"] * role_match
            + weights["embed"] * embed
            + weights["keys"] * overlap
            + weights["recency"] * recency
        )
        scored.append({
            **card,
            "_selection": {
                "score": round(score, 6),
                "role_match": role_match,
                "embedding_similarity": round(embed, 6),
                "key_overlap": round(overlap, 6),
                "recency": round(recency, 6),
            },
        })

    # Ties break on card_id so a fixed JD + card set always yields the same
    # order — the stability the acceptance criterion asks for.
    scored.sort(key=lambda c: (-c["_selection"]["score"], str(c.get("card_id"))))
    return scored[: (limit if limit is not None else top_n())]


# ── rendering under a bounded token budget ────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """~4 characters per token. Deliberately crude: the budget is a guard rail
    against unbounded growth, not an accounting of provider billing."""
    return (len(text) + 3) // 4


def _render_card(card: Dict) -> str:
    payload = card.get("payload") or {}
    job = payload.get("job") or {}
    emphasized = payload.get("emphasized") or {}
    ats = payload.get("ats") or {}

    lines = [
        f"- {job.get('title') or 'Untitled'}"
        f"{' at ' + job['company'] if job.get('company') else ''}"
        f" [{payload.get('role_family') or 'unclassified'}]"
    ]
    if ats.get("composite") is not None:
        line = f"  ATS composite: {ats['composite']}"
        if ats.get("delta") is not None:
            line += f" (delta {ats['delta']:+})"
        lines.append(line)
    if payload.get("user_score") is not None:
        lines.append(f"  User rated this tailoring: {payload['user_score']}/5")
    if emphasized.get("led_experience"):
        lines.append(f"  Led with experience: {emphasized['led_experience']}")
    if emphasized.get("led_project"):
        lines.append(f"  Led with project: {emphasized['led_project']}")
    if emphasized.get("skills"):
        lines.append(f"  Emphasized skills: {', '.join(emphasized['skills'][:10])}")

    # Only *active* rejections are pushed: a reversed one is retained on the card
    # for the policy work but must not be restated as a current preference.
    # Among the active ones, user-sourced are never elided — they are the whole
    # reason this tier exists, and the first thing a length trim would drop.
    user_rejected = active_rejections(payload, source="user")
    planner_rejected = active_rejections(payload, source="planner")
    for label, rows, cap in (
        ("User removed", user_rejected, None),
        ("Planner dropped", planner_rejected, 3),
    ):
        if not rows:
            continue
        shown = rows if cap is None else rows[:cap]
        lines.append(f"  {label}: " + "; ".join(
            f"{r.get('label') or r.get('item_key')}"
            + (f" ({r['rationale']})" if r.get("rationale") else "")
            for r in shown
        ))
    return "\n".join(lines)


def render_cards(cards: Sequence[Dict], budget: Optional[int] = None) -> str:
    """Render selected cards as a prompt block under a token budget.

    Cost is bounded twice over: each card is capped by construction at compile
    time (`_MAX_ITEMS` / `_MAX_SKILLS`), and this stops adding cards once the
    budget is spent. So the block's size is independent of how many jobs the
    user has accumulated — the property the acceptance criterion asks for.

    The single highest-ranked card is always rendered, even if it alone exceeds
    the budget: an over-long card should not silently switch the whole memory
    tier off.
    """
    if not cards:
        return ""
    budget = budget if budget is not None else token_budget()
    blocks: List[str] = []
    used = 0
    for card in cards:
        block = _render_card(card)
        cost = _estimate_tokens(block)
        if blocks and used + cost > budget:
            break
        blocks.append(block)
        used += cost
    return "\n".join(blocks)
