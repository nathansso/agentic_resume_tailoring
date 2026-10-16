"""The policy artifact (#241): one versioned home for every fitted threshold and guard tolerance.

`harness/policy.json` holds each value with the fit it came from (label set, recordings hash,
model, date, source issue). The modules that decide with a value read it through `get`, at import,
into the module-level names they always had (`support.TAU_BLOCK` and so on), so importers and
tests are unchanged. `_FALLBACK` is today's constants: it is what a module gets when the file is
missing or unreadable, and `tests/test_policy.py` pins it equal to the file. Nothing else in the
harness hard-codes a fitted value.

`version()` is stamped into every committed tailoring-tree node's provenance as
`policy_version` (`harness.tree.commit_node`), so a node says which policy it ran under. A value
changes only together with a new `version` and `digest` in the file; the test fails until both
move, and fails again until the fit scripts reproduce the value from the committed recordings.

Model-free and stdlib-only: this module imports nothing from ART.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

POLICY_PATH = Path(__file__).with_name("policy.json")
UNVERSIONED = "unversioned"          # `version()` when the file is missing or unreadable

# Today's values: the fallback when the artifact cannot be read. Keep equal to `policy.json`
# (`tests/test_policy.py::test_the_fallback_table_equals_the_artifact`).
_FALLBACK: Dict[str, float] = {
    "support.tau_block": 0.85,
    "support.tau_review": 0.35,
    "support_fallback.tau_block": 1.0,
    "support_fallback.tau_review": 0.5,
    "negative_pin.tau_block": 0.85,
    "negative_pin.tau_review": 0.15,
    "coverage.tau_cover": 0.65,
    "memory_gate.v1.tau_lo": 0.25,
    "memory_gate.v1.tau_hi": 0.65,
    "memory_gate.v1.tau_target": 0.75,
    "memory_gate.v2.tau_lo": 0.20,
    "memory_gate.v2.tau_hi": 0.65,
    "memory_gate.v2.tau_target": 0.75,
    "memory_gate.hi_floor": 0.5,
    "memory_gate.hard_mass": 0.25,
    "memory_gate.v1_would_be_writes": 29,
    "library.tau_variant": 0.50,
    "library.tau_baseline": 0.50,
    "library.tau_floor": 0.5,
    "library.variant_match_floor": 0.09,
    "library.fallback_track_floor": 0.30,
    "library.fallback_track_margin": 0.02,
    "guards.stuffing": 0.0,
    "guards.verb_entropy": 0.35,
    "guards.mtld": 0.10,
    "guards.duplication": 0.15,
    "guards.variant_drift": 0.35,
}


@lru_cache(maxsize=1)
def _doc() -> Dict[str, Any]:
    """The parsed artifact, or `{}` when it is missing, unreadable or not an object."""
    try:
        doc = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def reload() -> None:
    """Drop the cached file (tests that point `POLICY_PATH` elsewhere)."""
    _doc.cache_clear()


def _number(entry: Any) -> Optional[float]:
    value = entry.get("value") if isinstance(entry, dict) else None
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def get(key: str) -> float:
    """The value of `key` from the artifact, or its fallback. The type follows the fallback, so an
    integer stays an integer. An unknown key is a programming error."""
    fallback = _FALLBACK[key]
    found = _number(((_doc().get("values") or {}).get(key)))
    return fallback if found is None else type(fallback)(found)


def version() -> str:
    """The artifact's version, as stamped into node provenance."""
    raw = _doc().get("version")
    return str(raw) if raw not in (None, "") else UNVERSIONED


def digest(values: Optional[Dict[str, Any]] = None) -> str:
    """A short hash of the values alone (key and number, sorted): what `digest` in the file must
    equal. Provenance text and dates are not part of it."""
    values = _doc().get("values") if values is None else values
    flat = {k: v.get("value") for k, v in sorted((values or {}).items()) if isinstance(v, dict)}
    return hashlib.sha256(json.dumps(flat, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def file_sha256(path: Path) -> str:
    """The sha256 of a committed file with line endings normalised, so a Windows checkout and a
    Linux one agree. How the artifact's `recordings_sha256` and `labels_sha256` are computed."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


__all__ = ["POLICY_PATH", "UNVERSIONED", "digest", "file_sha256", "get", "reload", "version"]
