"""Verify that every profile's declared strata are true of its text (issue #172).

A sidecar declaration is free and unchecked. A fixture that *says*
`evidence_density: metric_poor` while carrying a number in every bullet does not
fail — it silently reports under the wrong slice, and the per-stratum table
looks complete while one of its rows means nothing. That is strictly worse than
not slicing at all, so every declaration is measured against the rendered
markdown here.

    python eval/profile_checks.py            # table + exit 1 on any failure
    python eval/profile_checks.py --verbose  # every measurement, passing or not

This is the dataset's own test suite, in the sense #172 asks for: the thresholds
below are *fixture-side* commitments, chosen after measuring the set rather than
borrowed, and each one separates the two populations it divides with a margin
recorded in its comment.

**On the legacy fixture.** `python eval/profile_checks.py --include-retired`
fails `benchmark_profile.md` on `level` and on `evidence_density`, and both are
real findings rather than bugs in this module. Its bullet "saving staff ten
hours weekly" contains `staff`, which `agents/ats_scorer.py::_LEVELS` maps to
the **lead** tier, and `_detect_level()` returns the *highest* tier matched
anywhere in the text. So the original benchmark candidate has been scored as a
lead-level résumé against entry-level postings on every run in this repo's
history, costing `role_level` (weight 0.10) most of its range — filed as **#181**.
The file is not edited — every historical figure was measured on it — it is
marked `"retired": true` in its sidecar and excluded from the default suite. See
`eval/README.md`.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.ats_scorer import _detect_level  # noqa: E402
from agents.redundancy import (  # noqa: E402
    STUFFING_DF_THRESHOLD,
    bullet_texts,
    leading_verb_entropy,
    new_information_ratio,
    term_document_frequency,
)
from eval.profile_fixture import load_profile  # noqa: E402
from eval.profile_meta import load_meta  # noqa: E402

# ── thresholds ────────────────────────────────────────────────────────────────
#
# Every value below was set by measuring the committed set, not adopted from a
# paper — the same discipline #172 requires of β, applied to the axes that ship
# first. The observed margin is recorded so a future edit that narrows one is
# visible as a narrowing rather than as a still-passing test.

# Share of bullets containing a digit. Observed: metric_rich 0.60–1.00,
# metric_poor 0.00. The gap is the whole interval between.
METRIC_RICH_MIN_DIGIT_SHARE = 0.55
METRIC_POOR_MAX_DIGIT_SHARE = 0.05

# Distinct skills claimed. Observed: specialist 15, generalist 27.
SPECIALIST_MAX_SKILLS = 18
GENERALIST_MIN_SKILLS = 24

# Redundancy, measured with `agents/redundancy.py` on the profile's own bullets.
# In plumbing mode the canned payload returns every source bullet verbatim, so
# these are also the numbers the benchmark reports for that mode.
#
# Observed: bearing min_new_information 0.000–0.111 and leading-verb entropy
# 0.782–0.812; clean 0.857–1.000 and 0.940–1.000. Term stuffing does not fire on
# either population — see `stuffing` in the module notes and #172's deviations.
BEARING_MAX_MIN_NEW_INFORMATION = 0.35
BEARING_MAX_LEADING_VERB_ENTROPY = 0.85

# Seniority tiers a profile in an intern/entry corpus may detect as. `mid` and
# above means a stray word (`senior`, `staff`, `principal`, `lead…`) has
# promoted the résumé and `role_level` is scoring a candidate the fixture does
# not describe.
ALLOWED_LEVELS = ("intern", "junior")


@dataclass(frozen=True)
class Finding:
    slug: str
    axis: str
    ok: bool
    detail: str

    def __str__(self) -> str:  # pragma: no cover - display
        mark = "ok  " if self.ok else "FAIL"
        return f"{mark} {self.slug:<52} {self.axis:<17} {self.detail}"


def measure(path: Path) -> Dict:
    """Every quantity the checks below read, for one profile markdown."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    fixture = load_profile(path)
    # The shape `agents/redundancy.py` consumes. Built from the parsed fixture
    # rather than from a tailoring run so a profile can be checked offline, with
    # no database, no model and no benchmark execution.
    content = {
        "experiences": [{"bullets": e["bullets"]} for e in fixture.experiences],
        "projects": [{"bullets": p["bullets"]} for p in fixture.projects],
        "skills_ranked": [{"name": s["name"]} for s in fixture.skills],
    }
    bullets = bullet_texts(content)
    df = term_document_frequency(content)
    ni = new_information_ratio(content)
    return {
        "text": text,
        "fixture": fixture,
        "bullets": bullets,
        "digit_share": (
            round(sum(1 for b in bullets if re.search(r"\d", b)) / len(bullets), 3)
            if bullets else 0.0),
        "skills": len(fixture.skills),
        "detected_level": _detect_level(text),
        "max_bullet_df": df["max_bullet_df"],
        "min_new_information": ni["min_new_information"],
        "mean_new_information": ni["mean_new_information"],
        "leading_verb_entropy": leading_verb_entropy(bullets),
    }


def redundancy_modes_fired(m: Dict) -> List[str]:
    """Which of #122's modes this profile trips, by name.

    Semantic duplication is deliberately absent: it needs an encoder, and a
    dataset check that loads a sentence-transformer would be too slow to run on
    every test invocation and would couple the fixture contract to model
    weights. The three lexical modes separate the populations on their own.
    """
    fired = []
    if m["max_bullet_df"] > STUFFING_DF_THRESHOLD:
        fired.append("stuffing")
    if (m["min_new_information"] is not None
            and m["min_new_information"] <= BEARING_MAX_MIN_NEW_INFORMATION):
        fired.append("dilution")
    if (m["leading_verb_entropy"] is not None
            and m["leading_verb_entropy"] <= BEARING_MAX_LEADING_VERB_ENTROPY):
        fired.append("monotony")
    return fired


def check_profile(path: Path) -> List[Finding]:
    """Findings for one profile. An undeclared axis is not checked — a profile
    may decline to claim a stratum, but may not claim one falsely."""
    path = Path(path)
    slug = path.stem
    strata = load_meta(path).strata
    m = measure(path)
    out: List[Finding] = []

    def add(axis: str, ok: bool, detail: str) -> None:
        out.append(Finding(slug, axis, ok, detail))

    # Checked whether or not the profile declares a level: reading above
    # `junior` is a property of the *text*, not of the declaration, and a
    # fixture that declines to declare one must not thereby escape the check.
    # That is exactly the retired `benchmark_profile.md`, which declares no
    # level and reads as `lead`.
    detected = m["detected_level"]
    declared = strata.get("level")
    add("level", detected in ALLOWED_LEVELS,
        f"declared {declared or 'nothing'}, ats_scorer detects {detected}")

    density = strata.get("evidence_density")
    share = m["digit_share"]
    if density == "metric_rich":
        add("evidence_density", share >= METRIC_RICH_MIN_DIGIT_SHARE,
            f"{share:.2f} of bullets carry a number (>= {METRIC_RICH_MIN_DIGIT_SHARE})")
    elif density == "metric_poor":
        add("evidence_density", share <= METRIC_POOR_MAX_DIGIT_SHARE,
            f"{share:.2f} of bullets carry a number (<= {METRIC_POOR_MAX_DIGIT_SHARE})")

    breadth = strata.get("breadth")
    skills = m["skills"]
    if breadth == "specialist":
        add("breadth", skills <= SPECIALIST_MAX_SKILLS,
            f"{skills} skills (<= {SPECIALIST_MAX_SKILLS})")
    elif breadth == "generalist":
        add("breadth", skills >= GENERALIST_MIN_SKILLS,
            f"{skills} skills (>= {GENERALIST_MIN_SKILLS})")

    redundancy = strata.get("redundancy")
    fired = redundancy_modes_fired(m)
    if redundancy == "bearing":
        add("redundancy", bool(fired),
            f"fires {', '.join(fired) or 'nothing'} "
            f"(min_new_info {m['min_new_information']}, "
            f"entropy {m['leading_verb_entropy']})")
    elif redundancy == "clean":
        add("redundancy", not fired,
            f"fires {', '.join(fired) or 'nothing'} "
            f"(min_new_info {m['min_new_information']}, "
            f"entropy {m['leading_verb_entropy']})")

    return out


def check_all(profiles_dir: Optional[Path] = None,
              include_retired: bool = False) -> List[Finding]:
    from eval.benchmark_suite import discover_profiles

    profiles_dir = Path(profiles_dir or ROOT / "eval" / "profiles")
    profiles = (sorted(profiles_dir.glob("*.md")) if include_retired
                else discover_profiles(profiles_dir))
    return [f for path in profiles for f in check_profile(path)]


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verbose", action="store_true",
                    help="print passing checks too")
    ap.add_argument("--include-retired", action="store_true",
                    help="check retired profiles as well (they are expected to fail)")
    ap.add_argument("--dir", type=Path, default=None)
    args = ap.parse_args(argv)

    findings = check_all(args.dir, include_retired=args.include_retired)
    failures = [f for f in findings if not f.ok]
    for finding in findings:
        if args.verbose or not finding.ok:
            print(finding)
    profiles = len({f.slug for f in findings})
    print(f"\n{len(findings) - len(failures)}/{len(findings)} checks pass "
          f"across {profiles} profiles")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
