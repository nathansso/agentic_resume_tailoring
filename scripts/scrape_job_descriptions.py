"""
Scrape real job descriptions into the tailoring-benchmark dataset (issues #51, #177).

Pulls postings from public, no-auth sources, restricts them to the **product's
actual target population** — intern and entry-level roles in five families —
verifies each employer structurally, and writes one JSON file per posting under
eval/jd_dataset/. The dataset is checked in so the benchmark is reproducible
offline; re-run this script only when you want to refresh it.

Coverage comes from `job_sources.py`, and its architecture is the point. A
hand-curated list of ATS tokens cannot be comprehensive: 32 of 80 guessed
tokens 404'd, and across ~110 boards this script could reach only 6 entry-level
AI-engineering postings. So the community GitHub boards are read first, their
apply links are mined for the ATS tokens that *actually exist*, and those boards
are then read in full. Descriptions for title-and-link rows are backfilled by
`job_descriptions.py`; employers are checked by `job_verification.py`.

Domain restriction (issue #177). The benchmark previously measured a mid-level
candidate against a corpus that included a *Senior* AI Engineer, and this
script's own EXCLUDE regex listed `intern` — it filtered out the population the
product serves. Now the level filter runs the other way: seniority markers are
rejected and intern/entry markers are required, either stated in the title or
implied by the posting's own years-of-experience language.

Every posting carries two labels the benchmark slices on, written to the file
rather than recomputed at load time — a task file must be reviewable and stable,
and a classifier that ran at load time would silently re-label the whole corpus
when it changed:

    role_family : data_science | data_engineering | ml_engineering
                  | software_engineering | ai_engineering
    level       : intern | entry

Usage:
    python scripts/scrape_job_descriptions.py                # refresh the corpus
    python scripts/scrape_job_descriptions.py --dry-run      # report, write nothing
    python scripts/scrape_job_descriptions.py --per-family 50

Each output file:
    {
      "id": "<company>_<slug>",
      "source": "<feed or ATS platform>",
      "company": "...", "title": "...", "location": "...", "url": "...",
      "role_family": "...", "level": "intern|entry", "posted": "<ISO date|null>",
      "verified": true, "verification": ["own ATS", "3 feeds"],
      "description": "<plain text>", "scraped_at": "<ISO date>"
    }
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import job_descriptions  # noqa: E402
import job_sources  # noqa: E402
import job_verification  # noqa: E402

DATASET_DIR = ROOT / "eval" / "jd_dataset"

# ── domain restriction (issue #177) ───────────────────────────────────────────
#
# Families are matched in declared order and the first hit wins, so a
# "Machine Learning Software Engineer" lands in ml_engineering rather than
# software_engineering. Insertion order is part of the contract; dict ordering
# is guaranteed in Python 3.7+, and the order must stay content-driven so a
# reorder is a deliberate, reviewable change (#158/#171 determinism rules).
ROLE_FAMILIES: "dict[str, re.Pattern]" = {
    family: re.compile(pattern, re.I)
    for family, pattern in [
        ("ml_engineering",
         r"machine learning engineer|\bml engineer\b|machine learning scientist"
         r"|\bml scientist\b|deep learning engineer|machine learning"),
        ("ai_engineering",
         r"\bai engineer\b|applied ai|\bgen ?ai\b|generative ai|\bllm\b"
         r"|\bai/ml\b|artificial intelligence engineer"),
        ("data_science",
         r"data scientist|data science|quantitative analyst|decision scientist"),
        ("data_engineering",
         r"data engineer|analytics engineer|data platform|data infrastructure"),
        ("software_engineering",
         r"software engineer|software developer|backend|back.end|frontend"
         r"|front.end|full.stack|fullstack|\bswe\b|platform engineer"),
    ]
}
ROLE_FAMILY_NAMES = tuple(ROLE_FAMILIES)

LEVELS = ("intern", "entry")

# Seniority markers. Roman numerals II+ are here because "Software Engineer II"
# is a promoted rung, while "Software Engineer I" is the entry rung and is
# matched by _ENTRY_TITLE below.
SENIOR_TITLE = re.compile(
    r"\bsenior\b|\bsr\.?\b|\bstaff\b|\bprincipal\b|\blead\b|\bdirector\b"
    r"|\bmanager\b|\bvp\b|head of|distinguished|architect|\bii+\b|\bfellow\b",
    re.I,
)
_INTERN_TITLE = re.compile(
    r"\bintern\b|internship|\bco-?op\b|summer 20\d\d|\bapprentice", re.I)
_ENTRY_TITLE = re.compile(
    r"new ?grad|new graduate|entry.level|university|early career|campus"
    r"|\bassociate\b|\bjunior\b|\bjr\.?\b|graduate program|\bI\b$|\b1\b$", re.I)

# Years-of-experience gate for postings whose title states no level. The
# *smallest* stated requirement is used: a posting asking "0-2 years" while
# mentioning "5 years preferred" elsewhere is still an entry posting.
MAX_ENTRY_YEARS = 2
_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:-|to|–|—)?\s*\d{0,2}\s*\+?\s*years?", re.I)

MIN_DESCRIPTION_CHARS = 800  # skip stub postings with no real requirements text


def _slug(text: str, max_len: int = 48) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:max_len]


def classify_family(title: str) -> "str | None":
    """First matching family in declared order, or None if the title is off-domain.

    Order is load-bearing: the specific families are declared before
    software_engineering so "Machine Learning Software Engineer" classifies as
    ml_engineering rather than being swallowed by the generic pattern.
    """
    for family, pattern in ROLE_FAMILIES.items():
        if pattern.search(title or ""):
            return family
    return None


def min_stated_years(description: str) -> "int | None":
    """Smallest years-of-experience figure the posting asks for, or None.

    Lenient on purpose. A posting that says "0-2 years required" and "5 years
    preferred" is an entry posting, so the minimum is the right summary; using
    the maximum would reject it on its own nice-to-have line.
    """
    values = [int(m.group(1)) for m in _YEARS_RE.finditer(description or "")]
    return min(values) if values else None


def classify_level(title: str, description: str) -> "str | None":
    """`intern`, `entry`, or None when the posting is out of the target domain.

    A seniority marker in the title is disqualifying outright — it is the most
    reliable signal on the page, and it is the one the old EXCLUDE regex had
    pointed the wrong way (issue #177). Absent any stated level, the posting's
    own years-of-experience language decides, so a plain "Machine Learning
    Engineer" asking for 0-2 years is correctly kept.
    """
    title = title or ""
    if _INTERN_TITLE.search(title):
        return "intern"
    if SENIOR_TITLE.search(title):
        return None
    if _ENTRY_TITLE.search(title):
        return "entry"
    years = min_stated_years(description)
    if years is not None and years <= MAX_ENTRY_YEARS:
        return "entry"
    return None


def build_posting(*, source: str, company: str, title: str, location: str,
                  url: str, description: str, posted: "str | None" = None,
                  verified: bool = False,
                  verification: "list | None" = None) -> "dict | None":
    """One candidate posting, or None if it fails the domain filters.

    Shared by every adapter so the family/level contract cannot drift between
    them.
    """
    if len(description) < MIN_DESCRIPTION_CHARS:
        return None
    family = classify_family(title)
    if family is None:
        return None
    level = classify_level(title, description)
    if level is None:
        return None
    return {
        "id": f"{_slug(company)}_{_slug(title)}",
        "source": source,
        "company": company,
        "title": title,
        "location": location,
        "url": url,
        "role_family": family,
        "level": level,
        "posted": posted,
        # Structural evidence the employer exists and is hiring (issue #177).
        # Recorded on the file rather than recomputed, so a corpus review can
        # see why each posting was admitted.
        "verified": verified,
        "verification": verification or [],
        "description": description,
        "scraped_at": date.today().isoformat(),
    }


def collect(verbose: bool = True) -> list[dict]:
    """Every in-domain, verified posting the sources can reach.

    Three stages, in this order for a reason (issue #177):

    1. **Aggregators first.** The community GitHub boards are what make coverage
       reach past big tech, and their apply links are also the only reliable way
       to learn which ATS board tokens actually exist — guessing them does not
       scale (32 of 80 hand-guessed tokens 404'd here).
    2. **Read every discovered ATS board in full**, which surfaces roles the
       aggregators never listed.
    3. **Backfill descriptions**, since aggregator rows are a title and a link.

    Verification runs last because corroboration is a property of the whole
    pull: how many independent feeds carry an employer cannot be known from one
    posting.
    """
    raw = job_sources.fetch_github_boards(verbose=verbose)
    tokens = job_sources.discover_ats_tokens(raw)
    if verbose:
        print("  discovered tokens: " +
              ", ".join(f"{k}={len(v)}" for k, v in sorted(tokens.items())))
    raw.extend(job_sources.fetch_ats(tokens, verbose=verbose))

    job_descriptions.fetch_missing(raw, verbose=verbose)

    counts = job_verification.feed_counts(raw)
    postings, rejected = [], {"unverified": 0, "domain": 0}
    for job in raw:
        feeds = counts.get(job_verification.company_key(job), 1)
        if not job_verification.is_verified(job, feeds):
            rejected["unverified"] += 1
            continue
        posting = build_posting(
            source=job.get("source", ""),
            company=job.get("company", ""),
            title=job.get("title", ""),
            location=job.get("location", ""),
            url=job.get("url", ""),
            description=job.get("text", ""),
            posted=job.get("posted"),
            verified=True,
            verification=job_verification.assess(job, feeds)["signals"],
        )
        if posting:
            postings.append(posting)
        else:
            rejected["domain"] += 1
    if verbose:
        print(f"  {len(raw)} raw → {len(postings)} in-domain and verified "
              f"(rejected {rejected['unverified']} unverified, "
              f"{rejected['domain']} off-domain/short)")
    return postings


def dedupe(candidates: list[dict]) -> dict:
    """Collapse the three ways one real opening appears more than once.

    Measured on the first 150-task pull, all three fired:

    * **Same id.** One role posted under several locations; ATS feeds return an
      entry per location.
    * **Same employer under two spellings.** `AEG` / `AEG Worldwide`,
      `Aquatic Capital` / `Aquatic`, `ASSYST, Inc.` / `Assyst` — the id is built
      from the company string, so a spelling variant slips past an id check.
      Keyed on the normalized company the verification layer already uses.
    * **Same description text under two ids.** `GDIT` and
      `General Dynamics Information Technology` posted one internship twice, and
      Microsoft posted one body under both an AI/ML title and a Data Platform
      title — **which landed the same document in two different role families.**
      That one matters most: a per-family contrast built on a shared document is
      partly comparing a posting with itself, and no amount of sample size fixes
      it.

    Description identity is checked **globally, before the per-family split**, so
    a body shared across families survives exactly once, in whichever family its
    title classified it into.

    Input is sorted on content first, so "first wins" is deterministic rather
    than dependent on feed order (#158/#171).
    """
    unique: dict[str, dict] = {}
    by_role: dict = {}          # normalized title → company keys already kept
    seen_description: set = set()
    for posting in sorted(candidates, key=lambda p: (p["company"], p["title"], p["id"])):
        if posting["id"] in unique:
            continue

        body = hashlib.sha256(posting["description"].encode("utf-8")).hexdigest()
        if body in seen_description:
            continue

        role = re.sub(r"[^a-z0-9]+", "", posting["title"].lower())
        key = _employer_tokens(posting["company"])
        if any(_same_employer(key, kept) for kept in by_role.get(role, ())):
            continue

        by_role.setdefault(role, []).append(key)
        seen_description.add(body)
        unique[posting["id"]] = posting
    return unique


def _employer_tokens(company: str) -> tuple:
    """Company name → its lowercase word tokens, corporate suffixes dropped."""
    words = re.findall(r"[a-z0-9]+", (company or "").lower())
    while words and words[-1] in _CORPORATE_SUFFIXES:
        words.pop()
    return tuple(words)


_CORPORATE_SUFFIXES = {"inc", "llc", "ltd", "corp", "corporation", "co",
                       "company", "plc", "gmbh", "sa", "ag", "nv", "pte"}


def _same_employer(a: tuple, b: tuple) -> bool:
    """Is one company name a *word-prefix* of the other?

    Compared on word tokens rather than on a stripped string, because the
    stripped form both over- and under-matches. Under: "AEG" → `aeg` and
    "AEG Worldwide" → `aegworldwide` are unequal, so exact matching misses a
    real duplicate. Over: a raw string prefix collapses "Meta" into "Metabase".

    Word tokens get every observed case right — `("aeg",)` is a prefix of
    `("aeg", "worldwide")`, while `("meta",)` is not a prefix of
    `("metabase",)` — with no length threshold to tune. The caller additionally
    requires an identical role title before treating this as a duplicate.
    """
    if not a or not b:
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return long[:len(short)] == short


def allocate(candidates: list[dict], per_family: int) -> list[dict]:
    """Up to `per_family` postings per family, deterministically and diversely.

    Two properties, both load-bearing:

    **Deterministic.** Ordered on *content* (company, title, id), never on API
    response order — the rule #158 and #171 each learned expensively, and a board
    API makes no ordering guarantee at all. Dedupes by id first, since the same
    role is frequently posted under several locations.

    **Spread across employers.** Taking the first N of a company-sorted list
    would fill a 30-slot family entirely from companies beginning with "A" —
    deterministic and badly unrepresentative, with 2,000+ candidates to choose
    from. So selection round-robins over companies: one posting from each, in
    company order, repeating until the quota fills. A company with many openings
    contributes more only once every other employer has contributed one.
    """
    unique = dedupe(candidates)

    kept: list[dict] = []
    for family in ROLE_FAMILY_NAMES:
        by_company: dict[str, list] = {}
        for p in unique.values():
            if p["role_family"] == family:
                by_company.setdefault(p["company"], []).append(p)

        chosen: list[dict] = []
        queues = [by_company[c] for c in sorted(by_company)]
        depth = 0
        while len(chosen) < per_family and any(len(q) > depth for q in queues):
            for q in queues:
                if len(q) > depth:
                    chosen.append(q[depth])
                    if len(chosen) == per_family:
                        break
            depth += 1
        kept.extend(chosen)
    return kept


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-family", type=int, default=30,
                    help="max postings kept per role family (issue #177: 30 gives "
                         "d~0.50 detectable on a paired per-family contrast, and "
                         "matches LongMemEval's per-ability slice)")
    ap.add_argument("--out", type=Path, default=DATASET_DIR, help="output directory")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be written without touching the dataset")
    ap.add_argument("--keep-existing", action="store_true",
                    help="do not clear the output directory first")
    args = ap.parse_args()

    candidates = collect()
    kept = allocate(candidates, args.per_family)

    print("\nper family:")
    for family in ROLE_FAMILY_NAMES:
        rows = [p for p in kept if p["role_family"] == family]
        interns = sum(1 for p in rows if p["level"] == "intern")
        short = "" if len(rows) >= args.per_family else "  ** SHORT **"
        print(f"  {family:<22} {len(rows):>3}  ({interns} intern / "
              f"{len(rows) - interns} entry){short}")

    if args.dry_run:
        print(f"\ndry run — {len(kept)} postings would be written to {args.out}")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    if not args.keep_existing:
        # A refresh replaces the corpus rather than layering on top of it:
        # leaving stale files behind is how the old mid-level and senior
        # postings would survive a domain restriction (issue #177).
        stale = [p for p in sorted(args.out.glob("*.json"))]
        for path in stale:
            path.unlink()
        if stale:
            print(f"\ncleared {len(stale)} existing task file(s)")
    for p in kept:
        (args.out / f"{p['id']}.json").write_text(
            json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    print(f"\n{len(kept)} postings written to {args.out}")
    return 0 if kept else 1


if __name__ == "__main__":
    raise SystemExit(main())
