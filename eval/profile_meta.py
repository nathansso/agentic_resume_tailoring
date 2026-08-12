"""Per-profile sidecar metadata for the benchmark (issue #172).

A profile is a résumé markdown file. Three things the harness needs cannot live
inside one:

* **Which stratum the profile isolates.** Not résumé content; it is a label
  *about* the fixture, and putting it in the markdown would either leak into the
  ingested text or need a comment convention the real parser must then ignore.
* **GitHub metrics.** `Project.metrics` is a JSON column populated by the GitHub
  ingest (`database/models.py`), and a résumé has nowhere to put stars or commit
  counts. Without them `agents/project_scorer.py::_github_signal()` returns
  `None` for every fixture project and the whole component is omitted from
  `_complexity()` — which is exactly what **#155's deviations recorded** as an
  unmeasurable change ("a fixture with GitHub metrics is the missing
  capability").
* **Distractor sets.** Plausible-but-irrelevant skills and projects that scale
  difficulty without moving the answer key (LongMemEval's haystack idea). They
  are deliberately *not* in the résumé text so that injecting them is a harness
  decision, and the same profile can run with and without.

So each `eval/profiles/<name>.md` may have a `eval/profiles/<name>.meta.json`
beside it. The sidecar is optional: a profile without one parses and runs, it
just contributes to no stratum slice.

    {
      "strata": {"role_family": "ml_engineering", "level": "entry",
                 "breadth": "specialist", "evidence_density": "metric_rich",
                 "redundancy": "clean"},
      "notes": "Hand-reviewed 2026-08-11.",
      "github_metrics": {
        "SemanticSearch-Lite": {"stars": 412, "languages": ["Python"],
                                "readme_length": 1800, "contributors": 3,
                                "author_commits": 155, "total_commits": 190}
      },
      "distractors": {"skills": ["COBOL"], "projects": []}
    }

`github_metrics` keys are project **names** as they appear in the markdown, and
the values are the shape `services.py::_build_repo_metrics()` produces — so the
seeded row is indistinguishable from one a real ingest would have written.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from eval.profile_ontology import cell_key, missing_strata, validate_strata

SIDECAR_SUFFIX = ".meta.json"

# Keys `_build_repo_metrics` emits. A sidecar naming anything else is refused:
# `_github_signal` only reads these, so an unknown key is silently inert and
# would leave the fixture author believing they had seeded a signal.
GITHUB_METRIC_KEYS = frozenset({
    "stars", "languages", "readme_length", "contributors",
    "author_commits", "total_commits", "project_type",
})


class ProfileMetaError(ValueError):
    """The sidecar is malformed, or disagrees with the profile it describes."""


class ProfileMeta:
    """One profile's sidecar. Empty and harmless when no file exists."""

    def __init__(self, strata: Optional[Dict[str, str]] = None,
                 github_metrics: Optional[Dict[str, Dict]] = None,
                 distractors: Optional[Dict[str, List]] = None,
                 notes: str = "", source: Optional[Path] = None):
        self.strata = dict(strata or {})
        self.github_metrics = dict(github_metrics or {})
        self.distractors = dict(distractors or {})
        self.notes = notes
        self.source = source

    @property
    def cell(self) -> str:
        return cell_key(self.strata)

    @property
    def missing(self) -> List[str]:
        return missing_strata(self.strata)

    def stratum(self, axis: str) -> Optional[str]:
        return self.strata.get(axis)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"ProfileMeta(strata={self.strata}, "
                f"github_metrics={len(self.github_metrics)}, "
                f"distractors={ {k: len(v) for k, v in self.distractors.items()} })")


def sidecar_path(profile_path: Path) -> Path:
    """`.../alex.md` → `.../alex.meta.json`."""
    profile_path = Path(profile_path)
    return profile_path.with_suffix("").with_name(
        profile_path.stem + SIDECAR_SUFFIX)


def load_meta(profile_path: Path) -> ProfileMeta:
    """Sidecar for a profile, or an empty one when it has none."""
    path = sidecar_path(profile_path)
    if not path.exists():
        return ProfileMeta(source=None)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileMetaError(f"{path}: not valid JSON — {exc}") from exc
    return parse_meta(raw, source=path)


def parse_meta(raw: Dict, source: Optional[Path] = None) -> ProfileMeta:
    label = str(source or "<sidecar>")
    if not isinstance(raw, dict):
        raise ProfileMetaError(f"{label}: expected a JSON object")

    def _section(key: str) -> Dict:
        """A present-but-wrong-typed section must fail, not be coerced.

        `raw.get(key) or {}` silently turns `[]` into `{}`, so a sidecar written
        with the wrong bracket would validate clean and contribute nothing —
        precisely the quiet failure this module exists to prevent.
        """
        value = raw.get(key)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ProfileMetaError(f"{label}: {key!r} must be an object")
        return value

    strata = _section("strata")
    validate_strata({str(k): str(v) for k, v in strata.items()}, source=label)

    metrics = _section("github_metrics")
    for project, values in sorted(metrics.items()):
        if not isinstance(values, dict):
            raise ProfileMetaError(
                f"{label}: github_metrics[{project!r}] must be an object")
        unknown = sorted(set(values) - GITHUB_METRIC_KEYS)
        if unknown:
            raise ProfileMetaError(
                f"{label}: github_metrics[{project!r}] has unknown key(s) "
                f"{', '.join(unknown)}. _github_signal reads only "
                f"{', '.join(sorted(GITHUB_METRIC_KEYS))}, so an unknown key is "
                "silently inert rather than a seeded signal.")

    distractors = _section("distractors")
    for key, values in sorted(distractors.items()):
        if not isinstance(values, list):
            raise ProfileMetaError(
                f"{label}: distractors[{key!r}] must be a list")

    return ProfileMeta(strata=strata, github_metrics=metrics,
                       distractors=distractors,
                       notes=str(raw.get("notes") or ""), source=source)


def check_projects_exist(meta: ProfileMeta, project_names: List[str]) -> None:
    """Every `github_metrics` key must name a project the profile actually has.

    A typo here is the quiet kind of failure this whole sidecar exists to avoid:
    the metrics would be seeded onto nothing, `_github_signal()` would keep
    returning `None`, and the fixture would look like it carried a signal it does
    not. Matched case-insensitively, since the markdown is the source of truth
    for spelling.
    """
    available = {name.strip().lower() for name in project_names}
    missing = sorted(name for name in meta.github_metrics
                     if name.strip().lower() not in available)
    if missing:
        raise ProfileMetaError(
            f"{meta.source or '<sidecar>'}: github_metrics names no such "
            f"project: {', '.join(missing)}. "
            f"The profile has: {', '.join(sorted(project_names))}")
