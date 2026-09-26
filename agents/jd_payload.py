"""Model-free readers over a compiled JD profile payload (issue #197).

`agents/jd_profile.py` imports the extraction stack (`langchain_core`, `llm`)
at module level, so anything that only needs to *read* a stored profile —
arbitration, keyword weights, the harness executor — imports these from here
instead and stays inside the harness import boundary
(`tests/test_harness_boundary.py`). `jd_profile` re-exports both names, so
existing callers are unchanged.

The deterministic half of the profile (compile, merge, digests) lives here too
(issue #192), so `open_job` can store a host-extracted profile without the
extraction stack. `jd_profile` re-exports all of it.
"""
import hashlib
import json
from typing import Any, Dict, List, Optional

from agents.ats_scorer import ATSScoringEngine, _detect_level
from agents.extraction_schemas import JDProfileExtraction, JDRequirementItem, RequirementType


def text_digest(description: str, version: int) -> str:
    """Digest of the JD text + a schema version, whitespace-normalized.

    An equal key means a stored artifact already describes this exact posting.
    Whitespace is normalized so a reflowed paste is not treated as a new
    posting.
    """
    normalized = " ".join((description or "").split())
    raw = f"{version}\x00{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def iter_requirements(
    payload: Optional[Dict], types: Optional[List[str]] = None,
) -> List[Dict]:
    """Requirements in source order, optionally filtered by type.

    The read path downstream consumers use. Returns a list rather than a
    generator so callers can count without exhausting it, and always in source
    order — filtering never reorders.
    """
    rows = [
        r for r in ((payload or {}).get("requirements") or [])
        if isinstance(r, dict)
    ]
    if types is not None:
        wanted = set(types)
        rows = [r for r in rows if r.get("type") in wanted]
    return rows


# Payload schema version. Bump when the *shape* of the compiled payload changes,
# or when the extraction prompt changes enough that stored profiles should be
# considered stale. Bumping invalidates every stored profile — and because
# re-extraction runs the edit-preserving merge, hand-corrected requirements
# survive the bump. That is the whole reason `edited` exists.
PROFILE_VERSION = 1

# Criticality is 1..5 by definition; the model is asked for that range but not
# constrained to it (see the note in extraction_schemas), so the compile clamps.
_CRITICALITY_MIN = 1
_CRITICALITY_MAX = 5
_CRITICALITY_DEFAULT = 3

# ── Keys and digests ─────────────────────────────────────────────────────────

def extraction_key(description: str, version: int = PROFILE_VERSION) -> str:
    """Digest of the JD text + schema version.

    An equal key means the stored profile already describes this exact posting,
    so extraction is skipped. This is what turns "extract once" into a property
    of the code. Whitespace is normalized so a reflowed paste is not treated as
    a new posting.
    """
    return text_digest(description, version)


def payload_digest(payload: Dict) -> str:
    """Stable digest of a compiled payload, for determinism assertions."""
    return hashlib.sha256(
        json.dumps(payload or {}, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


# ── Deterministic helpers ────────────────────────────────────────────────────

def _clean_terms(terms: Any) -> List[str]:
    """Lowercased, stripped, order-preserving-deduped terms.

    Lowercased to match `ats_scorer._extract_keywords`, which is what these
    terms will eventually be compared against. Order-preserving rather than
    sorted: within a requirement the model lists the head term first, and that
    is worth keeping.
    """
    out: List[str] = []
    seen = set()
    for term in terms or []:
        cleaned = str(term or "").strip().lower()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def _clamp_criticality(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return _CRITICALITY_DEFAULT
    return max(_CRITICALITY_MIN, min(_CRITICALITY_MAX, n))


def _clamp_confidence(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, min(1.0, f)), 3)


def title_terms(title: str) -> List[str]:
    """Deterministic fallback for title terms.

    The job title is the highest-signal, lowest-cost importance input there is
    (#125), so it must never be empty just because the model forgot to fill the
    field. Sorted here — unlike requirement terms there is no meaningful source
    order in a set of extracted keywords, and sorting keeps the payload stable.
    """
    return sorted(ATSScoringEngine._extract_keywords(title or ""))


def detect_role_level(description: str) -> str:
    """Seniority tier, reusing the scorer's existing detector.

    Deterministic and already tested, so it is not worth spending an LLM field
    on — and reusing it keeps the profile's `role_level` identical to the value
    `_role_level` scores against, rather than introducing a second opinion.
    """
    return _detect_level(description or "")



def compile_profile_payload(
    title: str, description: str, extraction: Optional[JDProfileExtraction],
) -> Dict:
    """Normalize an extraction into the stored payload. Pure and deterministic.

    Everything past the model call happens here, so the same extraction always
    compiles to the same payload and `payload_digest` is a real determinism
    check rather than a digest of model noise.

    Requirements keep their source order and are numbered by it. Requirements
    with no text are dropped rather than stored — an unlabelled requirement
    cannot be used as an NLI hypothesis downstream and cannot be meaningfully
    reviewed by a human either, which mirrors #21 dropping ungrounded notes.
    """
    requirements: List[Dict] = []
    items: List[JDRequirementItem] = list(getattr(extraction, "requirements", None) or [])
    for item in items:
        text = (getattr(item, "text", None) or "").strip()
        if not text:
            continue
        rtype = getattr(item, "type", RequirementType.REQUIRED)
        requirements.append({
            "text": text,
            "type": rtype.value if isinstance(rtype, RequirementType) else str(rtype),
            "criticality": _clamp_criticality(getattr(item, "criticality", None)),
            "terms": _clean_terms(getattr(item, "terms", None)),
            "source_section": (getattr(item, "source_section", None) or None),
            "confidence": _clamp_confidence(getattr(item, "confidence", None)),
            # Source-order position. Stored explicitly, not left implicit in the
            # list index, so #125 can read it directly and so a later change
            # that reorders the list is a visible inconsistency rather than a
            # silent loss of signal.
            "ordinal": len(requirements),
            # Set by the edit API, honored by merge_edits. Extraction never
            # produces an edited requirement.
            "edited": False,
        })

    extracted_title_terms = _clean_terms(getattr(extraction, "title_terms", None))
    return {
        "profile_version": PROFILE_VERSION,
        "requirements": requirements,
        "title_terms": extracted_title_terms or title_terms(title),
        "role_level": detect_role_level(description),
    }


# ── Edit-preserving merge ────────────────────────────────────────────────────

def merge_edits(old_payload: Optional[Dict], new_payload: Dict) -> Dict:
    """Carry hand-edited requirements forward across a re-extraction.

    The issue's hard rule is that re-extraction is explicit and versioned and
    never silent. The corollary this implements is that an explicit
    re-extraction still must not throw away corrections: a user who fixed a
    mis-parsed 'preferred' should not have to fix it again because the posting
    gained a typo fix.

    **Identity is text, never ordinal.** Matching on position looks reasonable
    and is wrong: a re-extraction that inserts or drops a requirement shifts
    every ordinal after it, so position-matching lets an edited requirement
    silently swallow a genuinely new one that happens to land at the same index.
    An edited requirement is matched to the fresh requirement whose text equals
    its `original_text` (the text as extracted, stashed by the edit path the
    first time a human rewrites it) or, when the text was never rewritten, its
    own `text`.

    An edited requirement matching nothing in the new extraction is **kept**,
    appended after the fresh ones. The posting may genuinely no longer state it,
    but dropping it would discard the user's work, which is the failure this
    function exists to prevent. Ordinals are renumbered over the merged result
    so source order stays contiguous.

    Returns the merged payload. `old_payload` None (first extraction) returns
    `new_payload` unchanged.
    """
    if not old_payload:
        return new_payload

    edited = [
        r for r in (old_payload.get("requirements") or [])
        if isinstance(r, dict) and r.get("edited")
    ]
    if not edited:
        return new_payload

    def _identity(req: Dict) -> str:
        return (req.get("original_text") or req.get("text") or "").strip()

    by_identity: Dict[str, Dict] = {}
    for req in edited:
        by_identity.setdefault(_identity(req), req)

    merged: List[Dict] = []
    consumed = set()
    for fresh in new_payload.get("requirements") or []:
        keep = by_identity.get((fresh.get("text") or "").strip())
        if keep is not None and id(keep) not in consumed:
            consumed.add(id(keep))
            merged.append(dict(keep))
        else:
            merged.append(dict(fresh))

    for orphan in edited:
        if id(orphan) not in consumed:
            merged.append(dict(orphan))

    for position, req in enumerate(merged):
        req["ordinal"] = position

    out = dict(new_payload)
    out["requirements"] = merged
    return out


# ── Read path ────────────────────────────────────────────────────────────────

def profile_terms(payload: Optional[Dict], types: Optional[List[str]] = None) -> List[str]:
    """Every term across the selected requirements, deduped, in source order."""
    out: List[str] = []
    seen = set()
    for req in iter_requirements(payload, types=types):
        for term in req.get("terms") or []:
            if term not in seen:
                seen.add(term)
                out.append(term)
    return out
