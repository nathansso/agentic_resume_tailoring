"""Shared selection helpers for the `matched_skills` mapping (issue #150).

`UserJobResult.matched_skills` is a `{skill_name: match_info}` dict produced by
`SkillMatcherAgent`, but it doubles as a carrier for internal metadata: after a
chat-driven tailor, `agents/chat.py` merges an `_explainability` block into it.
Any consumer that treats *every* key as a skill name therefore miscounts, and
because the metadata is only written on the chat path the error appears on
re-tailors only — it correlates with `is_revision` rather than being a constant
offset, which is what makes it harmful to the policy-learning arc (#114).

Underscore-prefixed keys are reserved for that metadata. This module is the one
place that knows it, so the filter is not re-derived at each call site. It is
deliberately dependency-free (stdlib typing only) so the scorer, the FastAPI
routers, the eval harness, and `services.py` can all import it without a cycle.

Note that filtering happens on the *name list*, never on the dict itself:
`agents/job_card.py` reads `matched_skills["_explainability"]` on purpose.
"""

from typing import Dict, List, Mapping, Optional

METADATA_PREFIX = "_"


def is_metadata_key(key: str) -> bool:
    """True when `key` is internal metadata rather than a matched skill name."""
    return isinstance(key, str) and key.startswith(METADATA_PREFIX)


def skill_names(matched_skills: Optional[Mapping]) -> List[str]:
    """Matched skill names with metadata keys removed, in insertion order."""
    return [k for k in (matched_skills or {}) if not is_metadata_key(k)]


def visible_matched_skills(matched_skills: Optional[Mapping]) -> Dict:
    """`matched_skills` minus its metadata keys, values untouched.

    Use when a consumer needs the match info too; use `skill_names` when only
    the names matter.
    """
    return {
        k: v for k, v in (matched_skills or {}).items() if not is_metadata_key(k)
    }
