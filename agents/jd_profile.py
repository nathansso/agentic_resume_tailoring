"""JDProfile — the job description, extracted once (issue #121).

ARTie has a codified, persisted, hand-editable profile of the **candidate**: the
knowledge graph. It had nothing equivalent for the **job**. Every JD-derived
quantity was recomputed inline from raw text on every run — `_keyword_coverage`
and `_role_level` in `agents/ats_scorer.py`, `_jd_token_counts` in
`agents/keyword_planner.py`. This module extracts that structure once so it can
be persisted, inspected, corrected, and reused.

Three properties define it:

1. **Determinism comes from persistence, not from the model.** An LLM call is
   not byte-stable, so "two runs produce the same profile" cannot be a property
   of the extraction. It is enforced by `extraction_key`: a digest of the JD
   text and `PROFILE_VERSION` that short-circuits the second run before any
   model is reached. Everything after the single extraction call is a
   deterministic normalization, and `payload_digest` makes that directly
   assertable.

2. **Source order is load-bearing.** `requirements[]` is stored in the order the
   posting states them, and each requirement carries its `ordinal`. Ordinal
   position within a requirements list is one of #125's importance signals, and
   it is the one signal that cannot be recovered later — repetition counts can
   always be recomputed from the raw text, but a list that has been sorted or
   grouped by `type` has destroyed its own ordering irreversibly. So nothing in
   this module reorders requirements, and a test pins it.

3. **Extraction errors are the standing risk.** A "preferred" mis-parsed as
   "required" biases every downstream decision for that job with no visible
   symptom — the same failure mode as #69/#96 on the ingestion side, and it gets
   the same mitigations: a per-requirement `confidence`, an `edited` flag so a
   human correction is never silently overwritten by a later re-extraction, and
   re-extraction that is explicit and versioned rather than automatic.

Nothing here writes to the database. Persistence, the cache check, and the
event-driven rebuild live in `services.rebuild_jd_profile`, mirroring how
`agents/job_card.py` stays pure against `services.rebuild_job_card`.

This issue produces the artifact only. Weighting the terms is #125 (which fills
the `weights` slot) and consuming it in scoring is #125/#126 — so no scorer
reads this module yet, deliberately.
"""
import logging
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate

from agents.extraction_schemas import JDProfileExtraction
# The deterministic half lives in the model-free jd_payload (#192); re-exported.
from agents.jd_payload import (  # noqa: F401
    PROFILE_VERSION, _CRITICALITY_DEFAULT, _CRITICALITY_MAX, _CRITICALITY_MIN,
    _clamp_confidence, _clamp_criticality, _clean_terms, compile_profile_payload,
    detect_role_level, extraction_key, iter_requirements, merge_edits, payload_digest,
    profile_terms, text_digest, title_terms,
)
from llm import ModelRole, get_extractor

logger = logging.getLogger(__name__)

_EXTRACT_SYSTEM = (
    "You decompose job postings into atomic, individually-checkable "
    "requirements.\n\n"
    "Rules:\n"
    "- One requirement per statement. Split conjunctions: 'Python and Go' is "
    "two requirements, not one.\n"
    "- Write each requirement as a standalone sentence about the candidate. It "
    "will later be checked on its own, with no access to the rest of the "
    "posting.\n"
    "- Preserve the order the posting states them in. Do not group by type or "
    "sort by importance.\n"
    "- Classify type from the posting's own framing, not from your judgement of "
    "what matters: a must-have is 'required', a nice-to-have or bonus is "
    "'preferred', and something mentioned in passing that is not a "
    "qualification at all is 'incidental'.\n"
    "- Judge criticality from evidence inside this posting: terms in the job "
    "title matter most, then placement under a requirements heading, then how "
    "often the posting repeats the term, then how early it is listed.\n"
    "- Extract only requirements the posting actually states. Do not add "
    "conventional expectations for the role that are not written down.\n"
    "- Set a low confidence rather than guessing when a requirement's framing "
    "is genuinely ambiguous."
)

_EXTRACT_USER = (
    "Job title: {title}\n"
    "Company: {company}\n\n"
    "Job posting:\n{description}\n\n"
    "Decompose this posting into its requirements, in the order they appear, "
    "and list the meaningful terms from the job title."
)


# ── Extraction ───────────────────────────────────────────────────────────────

def extract_profile(
    title: str, company: str, description: str,
) -> Optional[JDProfileExtraction]:
    """The single LLM call. Returns None on any failure.

    Goes through the #142 `get_extractor` seam, so output is schema-validated by
    `with_structured_output` and traced by the LangSmith scaffold — there is no
    `JsonOutputParser` and no `json.loads` on this path.

    Failure returns None rather than raising: an absent profile reproduces
    today's behavior exactly, so a bad extraction must never take down the
    analyze or tailor run that triggered it.
    """
    if not (description or "").strip():
        return None
    prompt = ChatPromptTemplate.from_messages([
        ("system", _EXTRACT_SYSTEM),
        ("user", _EXTRACT_USER),
    ])
    try:
        extractor = get_extractor(role=ModelRole.EXTRACT, schema=JDProfileExtraction)
        return extractor.invoke(prompt.format_messages(
            title=title or "", company=company or "", description=description,
        ))
    except Exception as exc:
        logger.warning("JD profile extraction failed: %s", exc)
        return None
