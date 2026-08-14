"""Audit `eval/jd_dataset/` against what a pasted job description looks like.

    python scripts/audit_jd_corpus.py                 # summary, exit 1 on errors
    python scripts/audit_jd_corpus.py --detail truncated
    python scripts/audit_jd_corpus.py --json

Every benchmark task is replayed through the exact user flow: `_run_task` posts
`task["description"]` to `POST /api/jobs/{id}/description`, which is the endpoint
the paste box calls. So the corpus is only a valid measurement of the product if
each stored description is *the kind of text a user would paste* — the rendered
posting, whole, as plain text.

It is not automatically that. The corpus was assembled by fetching bodies from
ATS APIs and stripping HTML (`scripts/job_descriptions.py`,
`scripts/job_sources.py`), and each of those steps can produce text a browser
would never have put on the clipboard: a body cut at a byte ceiling, entities
left as `&amp;`, block tags removed without a separator so two words fuse,
apostrophes replaced by spaces. None of that is visible in a metrics table —
every number stays well-formed while the input degrades, which is exactly the
class of defect #177 hit three separate times.

The checks are grouped by what consumes the text:

* **schema** — the fields `eval/tailoring_benchmark.py::load_tasks` and the
  per-stratum slices read.
* **fidelity** — whether the text is a faithful rendering of the posting:
  truncation, HTML leakage, encoding damage, fused words, stripped apostrophes.
* **structure** — whether the shape survived. `agents/jd_profile.py` keeps
  `requirements[]` in source order and treats ordinal position as an importance
  signal (#125), which requires the requirement list to still *be* a list;
  `scripts/job_descriptions.py::strip_html` documents the same dependency.
* **content** — whether the body is a job description at all, and whether its
  stated seniority agrees with the `level` label the corpus reports it under.

Severity is `error` for anything that makes a task an invalid measurement, and
`warn` for degraded fidelity that is still measurable. Both are reported; only
`error` fails the build.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.ats_scorer import _detect_level  # noqa: E402
from job_descriptions import MAX_TEXT  # noqa: E402
from scrape_job_descriptions import (  # noqa: E402
    SENIOR_TITLE,
    classify_family,
    classify_level,
)

DATASET_DIR = ROOT / "eval" / "jd_dataset"

REQUIRED_FIELDS = ("id", "source", "company", "title", "url", "description",
                   "role_family", "level", "verified")
ROLE_FAMILIES = ("data_science", "data_engineering", "ml_engineering",
                 "software_engineering", "ai_engineering")
LEVELS = ("intern", "entry")

# A body whose length lands exactly on a fetch ceiling was cut, not written —
# no posting is that length by coincidence. The current ceiling is imported
# rather than restated, because a second copy of it here is how this check
# silently started flagging *repaired* bodies as truncated. 6,000 is kept beside
# it as the historical ceiling: bodies that could not be re-fetched (expired
# listing, 403) still carry a cut at that length and must stay visible.
LEGACY_MAX_TEXT = 6000
CEILINGS = (MAX_TEXT, LEGACY_MAX_TEXT)

# A posting shorter than this is a stub — a title and an apply button, not a
# description something can be tailored against.
MIN_DESCRIPTION = 600
SHORT_DESCRIPTION = 1200

# Seniority tier a JD may state, given #177 restricted the corpus to
# intern/entry. `_detect_level` returns the highest tier matched anywhere, and
# `role_level` (weight 0.10) scores the résumé's tier against it.
EXPECTED_JD_LEVELS = {"intern": ("intern",), "entry": ("intern", "junior")}

_TAG_RE = re.compile(r"</?[a-zA-Z][^>\n]{0,80}>")
# Hex numeric entities are included deliberately: `&#xa0;` was the form that
# survived a single unescape of a double-escaped body, and an earlier version of
# this pattern matched only the named and decimal forms — so the check passed
# while the corpus carried the residue.
_ENTITY_RE = re.compile(r"&(?:[a-zA-Z]{2,10}|#\d{2,5}|#x[0-9a-fA-F]{2,6});")
_MOJIBAKE_RE = re.compile(r"�|Ã[\x80-\xbf©®]|â€[\x9c\x9d\x99\x94]|Â[\xa0-\xbf]")
_CONTROL_RE = re.compile(r"[ ​‎‏﻿\t\r]")
# "improvements.We deliver" — a block boundary removed with no separator.
_PERIOD_JOIN_RE = re.compile(r"[a-z]{2}\.[A-Z][a-z]{2}")
# "today s technology", "we re hiring" — an apostrophe entity replaced by a
# space instead of by an apostrophe.
_STRIPPED_APOSTROPHE_RE = re.compile(r"\b(?:we|you|it|don|doesn|isn|aren|won|can|that|there|here|what|let|today|company)\s+(?:s|t|re|ve|ll|d|m)\b",
                                     re.I)
_BULLET_LINE_RE = re.compile(r"^\s*[-•*•●·]\s+\S", re.M)

_REQUIREMENT_MARKERS = (
    "requirement", "qualification", "what you'll need", "what you will need",
    "what you bring", "who you are", "skills", "experience with",
    "you have", "basic qualifications", "minimum qualifications",
    "preferred", "must have", "we're looking for", "we are looking for",
)
_RESPONSIBILITY_MARKERS = (
    "responsibilit", "what you'll do", "what you will do", "role", "duties",
    "you will", "day to day", "day-to-day", "about the job",
)


@dataclass(frozen=True)
class Issue:
    task_id: str
    check: str
    severity: str          # "error" | "warn"
    detail: str


# ── checks ────────────────────────────────────────────────────────────────────

def check_schema(task: Dict, path: Path) -> Iterable[Issue]:
    tid = task.get("id") or path.stem

    def bad(check: str, detail: str) -> Issue:
        return Issue(tid, check, "error", detail)

    for field in REQUIRED_FIELDS:
        if field not in task:
            yield bad("schema", f"missing field {field!r}")
        elif field != "verified" and not str(task[field] or "").strip():
            yield bad("schema", f"empty field {field!r}")

    if task.get("id") != path.stem:
        yield bad("schema", f"id {task.get('id')!r} != filename {path.stem!r}")
    if task.get("role_family") not in ROLE_FAMILIES:
        yield bad("schema", f"role_family {task.get('role_family')!r}")
    if task.get("level") not in LEVELS:
        yield bad("schema", f"level {task.get('level')!r}")
    if task.get("verified") is not True:
        yield bad("schema", "verified is not true")
    if not str(task.get("url") or "").startswith("http"):
        yield bad("schema", f"url {task.get('url')!r}")


def check_fidelity(task: Dict, path: Path) -> Iterable[Issue]:
    """Is this the text a browser would have put on the clipboard?"""
    tid = task.get("id") or path.stem
    text = task.get("description") or ""

    if len(text) < MIN_DESCRIPTION:
        yield Issue(tid, "too_short", "error", f"{len(text)} chars")
    elif len(text) < SHORT_DESCRIPTION:
        yield Issue(tid, "short", "warn", f"{len(text)} chars")

    # Truncation. The length test catches a cut at a ceiling exactly; the
    # ending test catches a body cut somewhere else (a feed's own limit, a
    # paginated fetch), which the length test cannot see.
    if len(text) in CEILINGS:
        yield Issue(tid, "truncated", "error",
                    f"{len(text)} chars — exactly the fetch ceiling, "
                    f"ends: …{text[-60:].strip()!r}")
    elif text and not text.rstrip().endswith((".", "!", "?", ":", ")", "]", "”", '"')):
        yield Issue(tid, "unterminated", "warn",
                    f"ends mid-sentence: …{text[-60:].strip()!r}")

    tags = _TAG_RE.findall(text)
    if tags:
        yield Issue(tid, "html_tag", "error",
                    f"{len(tags)} residual tag(s): {sorted(set(tags))[:5]}")

    entities = _ENTITY_RE.findall(text)
    if entities:
        yield Issue(tid, "html_entity", "error",
                    f"{len(entities)} unescaped: {sorted(set(entities))[:5]}")

    if _MOJIBAKE_RE.search(text):
        yield Issue(tid, "mojibake", "error",
                    f"{_MOJIBAKE_RE.findall(text)[:3]}")

    control = _CONTROL_RE.findall(text)
    if control:
        yield Issue(tid, "control_chars", "warn",
                    f"{len(control)} of {sorted({repr(c) for c in control})}")

    joins = _PERIOD_JOIN_RE.findall(text)
    if joins:
        yield Issue(tid, "fused_words", "warn",
                    f"{len(joins)} sentence boundaries with no space: {joins[:3]}")

    stripped = _STRIPPED_APOSTROPHE_RE.findall(text)
    if stripped:
        yield Issue(tid, "stripped_apostrophe", "warn",
                    f"{len(stripped)} occurrence(s), e.g. "
                    f"{_STRIPPED_APOSTROPHE_RE.search(text).group(0)!r}")


def check_structure(task: Dict, path: Path) -> Iterable[Issue]:
    """Did the posting's shape survive the HTML strip?"""
    tid = task.get("id") or path.stem
    text = task.get("description") or ""
    lines = [line for line in text.splitlines() if line.strip()]

    if len(lines) < 5:
        yield Issue(tid, "no_line_structure", "warn",
                    f"{len(lines)} non-empty line(s) — the section boundaries "
                    "jd_profile keys on are gone")
    longest = max((len(line) for line in lines), default=0)
    if longest > 1500:
        yield Issue(tid, "wall_of_text", "warn",
                    f"longest line is {longest} chars")
    if not _BULLET_LINE_RE.search(text):
        yield Issue(tid, "no_bullets", "warn",
                    "no bulleted lines — requirement ordinals (#125) come from "
                    "a list that is still a list")


def check_content(task: Dict, path: Path) -> Iterable[Issue]:
    """Is it a job description, and does it match the label it is filed under?"""
    tid = task.get("id") or path.stem
    text = task.get("description") or ""
    lower = text.lower()

    if not any(marker in lower for marker in _REQUIREMENT_MARKERS):
        yield Issue(tid, "no_requirements", "warn",
                    "no requirements/qualifications section — nothing to tailor "
                    "against, and keyword_coverage scores against prose")
    if not any(marker in lower for marker in _RESPONSIBILITY_MARKERS):
        yield Issue(tid, "no_responsibilities", "warn",
                    "no responsibilities section")

    declared = task.get("level")
    detected = _detect_level(text)
    if declared in EXPECTED_JD_LEVELS and detected not in EXPECTED_JD_LEVELS[declared]:
        yield Issue(tid, "level_mismatch", "warn",
                    f"filed as {declared}, ats_scorer reads the posting as "
                    f"{detected} — role_level scores against the detected tier, "
                    "not the label")


def check_domain(task: Dict, path: Path) -> Iterable[Issue]:
    """Is this still an intern / entry-level posting?

    The stored `role_family` and `level` were decided at scrape time, from a
    body that may since have been re-fetched and is now longer — and #177's
    whole point is that the corpus matches the population the product targets.
    Re-deciding here, from the committed title and the committed body, is what
    makes "intern and entry-level only" an invariant rather than a claim about
    one afternoon's scrape. A label that no longer follows from the text is an
    error: it silently moves a posting into a stratum it does not belong to,
    and every per-family and per-level figure inherits that.
    """
    tid = task.get("id") or path.stem
    title = task.get("title") or ""
    description = task.get("description") or ""

    if SENIOR_TITLE.search(title):
        yield Issue(tid, "out_of_domain", "error",
                    f"seniority marker in title: {title!r}")

    level = classify_level(title, description)
    if level is None:
        yield Issue(tid, "out_of_domain", "error",
                    f"classify_level rejects {title!r} — neither intern nor entry")
    elif level != task.get("level"):
        yield Issue(tid, "level_relabelled", "error",
                    f"filed as {task.get('level')}, classifies as {level}")

    family = classify_family(title)
    if family != task.get("role_family"):
        yield Issue(tid, "family_relabelled", "error",
                    f"filed as {task.get('role_family')}, "
                    f"{title!r} classifies as {family}")


# An application form is not a job description. These bodies were fetched from a
# page that inlined the form, so the text carries dropdown options and field
# labels a user's paste of the posting never would.
_FORM_SCAFFOLD_RE = re.compile(
    r"no answer\s+yes\s+no|are you 18 years|desired salary|please select|"
    r"check all that apply|first name \*|resume/cv \*", re.I)


def check_boilerplate(task: Dict, path: Path) -> Iterable[Issue]:
    tid = task.get("id") or path.stem
    text = task.get("description") or ""
    if _FORM_SCAFFOLD_RE.search(text):
        yield Issue(tid, "form_scaffolding", "warn",
                    "body contains application-form fields, not just the posting: "
                    f"{_FORM_SCAFFOLD_RE.search(text).group(0)!r}")


CHECKS = (check_schema, check_fidelity, check_structure, check_content,
          check_domain, check_boilerplate)


def audit_task(task: Dict, path: Path) -> List[Issue]:
    return [issue for check in CHECKS for issue in check(task, path)]


def audit_corpus(dataset_dir: Path = DATASET_DIR) -> List[Issue]:
    paths = sorted(Path(dataset_dir).glob("*.json"))
    issues: List[Issue] = []
    by_description: Dict[str, List[str]] = defaultdict(list)

    for path in paths:
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            issues.append(Issue(path.stem, "unreadable", "error", str(exc)))
            continue
        issues += audit_task(task, path)
        text = (task.get("description") or "").strip()
        if text:
            by_description[text].append(task.get("id") or path.stem)

    # Corpus-level: one description under two ids makes a per-family contrast
    # partly compare a posting with itself — the defect #177 found in the
    # Microsoft pair and fixed for the pull it shipped.
    for ids in by_description.values():
        if len(ids) > 1:
            for tid in sorted(ids):
                issues.append(Issue(tid, "duplicate_description", "error",
                                    f"shared with {', '.join(sorted(set(ids) - {tid}))}"))
    return issues


# ── reporting ─────────────────────────────────────────────────────────────────

def summarize(issues: List[Issue], total: int) -> str:
    by_check: Dict[str, List[Issue]] = defaultdict(list)
    for issue in issues:
        by_check[issue.check].append(issue)

    severity = {check: group[0].severity for check, group in by_check.items()}
    lines = [f"{total} postings audited\n",
             f"{'check':<22}{'severity':<10}{'tasks':<8}{'share'}",
             "-" * 52]
    for check in sorted(by_check, key=lambda c: (severity[c] != "error",
                                                 -len({i.task_id for i in by_check[c]}))):
        affected = len({i.task_id for i in by_check[check]})
        lines.append(f"{check:<22}{severity[check]:<10}{affected:<8}"
                     f"{affected / total:.0%}" if total else check)
    clean = total - len({i.task_id for i in issues})
    errors = len({i.task_id for i in issues if i.severity == "error"})
    lines += ["",
              f"{clean}/{total} postings raise nothing",
              f"{errors}/{total} raise at least one error-severity issue"]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=DATASET_DIR)
    ap.add_argument("--detail", nargs="*", default=None,
                    help="list every task raising these checks (no value: all)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    total = len(sorted(Path(args.dir).glob("*.json")))
    issues = audit_corpus(args.dir)

    if args.json:
        print(json.dumps([issue.__dict__ for issue in issues], indent=2))
    else:
        print(summarize(issues, total))
        if args.detail is not None:
            wanted = set(args.detail) or {i.check for i in issues}
            for issue in issues:
                if issue.check in wanted:
                    print(f"\n{issue.severity:<6}{issue.check:<22}{issue.task_id}"
                          f"\n      {issue.detail}")

    return 1 if any(i.severity == "error" for i in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
