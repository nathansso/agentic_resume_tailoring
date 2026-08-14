"""Candidate attribute ontology for the benchmark profile set (issue #172).

The benchmark ran one profile against every JD and reported a single pooled
number per metric, so "weak" was the only finding available and "weak on
career-changers" was not expressible. This module names the axes a profile can
vary along, so a profile declares which variable it isolates and the harness can
slice every metric by it.

**Generate from the ontology, then hand-rewrite every profile.** LongMemEval
(arXiv:2410.10813) built 164 attributes across five categories, generated one
attribute-focused background per attribute, and then had three in-house NLP
researchers manually filter and rewrite all 500 questions — roughly 400 human
hours. WikiDYK (arXiv:2505.12306) reached 12,290 facts and 77,180 questions with
**no human validation**, and its own Limitations section carries that as the
weakness. Generation without review is the failure mode with a named example.

**Why n=3 per cell and not n=1.** With one profile per stratum, stratum and
profile identity are perfectly confounded: a difference between the "breadth"
profile and the "evidence density" profile is equally explained by that one
synthetic person's wording, company names, or bullet phrasing. Every paper this
benchmark borrows from avoids that by carrying many instances per condition —
Codified Character Logic (arXiv:2505.07705) reports main vs minor characters
separately across 83 characters and 5,141 scenes; PersonaAgent
(arXiv:2506.06254) evaluates on LaMP-style per-user benchmarks. 83 is not
available to one person; 3 per cell is, and it separates the stratum from the
individual.

**Every profile is synthetic.** `Alex Rivera` in `benchmark_profile.md` is the
precedent and it is not negotiable: no real user résumé ever enters `eval/`.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# ── strata ────────────────────────────────────────────────────────────────────
#
# Declaration order is the reporting order and part of the contract: a
# per-stratum table must not reorder itself between runs (#158/#171). Keys are
# what a profile's sidecar names; values are the closed set of allowed values.

STRATA: Dict[str, Tuple[str, ...]] = {
    # Primary stratum. Was `seniority` before #177 restricted the corpus to
    # intern/entry — inside that domain seniority collapses to a narrow
    # intern-vs-entry axis and no longer earns the primary slot, while role
    # family is what the JD corpus is now stratified on too.
    "role_family": ("data_science", "data_engineering", "ml_engineering",
                    "software_engineering", "ai_engineering"),
    # Replaces the old new-grad/mid/senior axis. Matches the JD-side `level`
    # label so a candidate stratum can be crossed with a posting stratum.
    "level": ("intern", "entry"),
    # Drives selection pressure and the skills cap.
    "breadth": ("specialist", "generalist"),
    # `quantify` has nothing to work with in the poor case.
    "evidence_density": ("metric_rich", "metric_poor"),
    # #122's four redundancy modes cannot fire on a fixture a human wrote to be
    # non-redundant; `bearing` is the variant that gives them something to find.
    "redundancy": ("clean", "bearing"),
}

STRATUM_NAMES: Tuple[str, ...] = tuple(STRATA)

# The axes a per-stratum table is sliced on by default. `role_family` is primary;
# the hazard axes are reported beside it. `level` is included because it crosses
# with the JD corpus's own label.
REPORTED_STRATA: Tuple[str, ...] = (
    "role_family", "level", "breadth", "evidence_density", "redundancy")

# Profiles per (role_family x hazard variant) cell. Below this, a "stratum
# difference" is indistinguishable from one synthetic person's idiosyncrasy.
MIN_PROFILES_PER_CELL = 3


class OntologyError(ValueError):
    """A profile declared a stratum or value the ontology does not define."""


def validate_strata(strata: Dict[str, str], source: str = "<profile>") -> None:
    """Raise unless every declared stratum and value is in the closed set.

    Fails on unknown *keys* as well as unknown values: a typo like
    `evidence_denisty` would otherwise be silently ignored and the profile would
    report under a stratum it never declared, which is worse than not slicing at
    all — the table would look complete.
    """
    for key, value in sorted(strata.items()):
        if key not in STRATA:
            raise OntologyError(
                f"{source}: unknown stratum {key!r}. "
                f"Known: {', '.join(STRATUM_NAMES)}")
        if value not in STRATA[key]:
            raise OntologyError(
                f"{source}: stratum {key!r} has value {value!r}, "
                f"expected one of {', '.join(STRATA[key])}")


def missing_strata(strata: Dict[str, str]) -> List[str]:
    """Reported axes this profile does not declare, in declaration order.

    A profile need not declare every axis — an unset axis simply does not
    contribute to that slice — but the harness reports which are missing rather
    than pretending the table is complete.
    """
    return [name for name in REPORTED_STRATA if name not in strata]


def cell_key(strata: Dict[str, str], axes: Tuple[str, ...] = REPORTED_STRATA) -> str:
    """Stable label for the cell a profile sits in, e.g.
    `role_family=ml_engineering|level=intern`.

    Built in declaration order rather than dict order so the same cell produces
    the same key regardless of how the sidecar was written.
    """
    return "|".join(f"{axis}={strata[axis]}" for axis in axes if axis in strata)
