"""Seniority-tier detection, shared by the ATS scorer and the JD scraper (#181).

Model-free and import-light on purpose: `agents/ats_scorer.py`, `agents/jd_payload.py`,
`eval/profile_checks.py`, `scripts/audit_jd_corpus.py` and
`scripts/scrape_job_descriptions.py` all read a tier from here, so a posting is
labelled the same way at scrape time, at audit time and at scoring time.

What replaced what (issue #181). `_detect_level` used to match bare substrings
and return the *highest* tier found anywhere in the text, so `leading`,
`staffing`, `principal component analysis`, `hiring manager` and `associated
with` each promoted a whole document, and 124 of 150 intern/entry postings read
at the wrong tier. The rules now are:

1. **Word boundaries.** Nothing here matches inside another word.
2. **Titles carry the signal.** A tier word counts when it sits in a *title*:
   the posting's heading, or a résumé's role line. `lead`, `staff` and
   `principal` additionally need a role noun next to them (`Staff Software
   Engineer`, not `staff ten hours`), and `manager` is never read out of prose.
3. **Title over body.** A tier found in the title is final. Only a title with no
   tier consults the body, and then only through self-referential phrases
   (`entry-level`, `this internship`, `direct reports`) and stated years of
   experience, never through a bare tier word that may describe someone else.
4. **Unknown is a tier.** No evidence returns `unknown`, which the scorer scores
   neutrally, rather than the old `mid` default that penalised every intern/entry
   posting that never states a level.

A candidate (`kind="candidate"`) is read the same way with one difference: a
résumé has no heading, so the "title" is every short, unbulleted role line.
"""

import re
from typing import Dict, Iterable, List, Optional, Tuple

INTERN, JUNIOR, MID, SENIOR, LEAD, MANAGER = (
    "intern", "junior", "mid", "senior", "lead", "manager")
UNKNOWN = "unknown"

# Ascending. The gap between two tiers is the distance between their indices.
LEVEL_ORDER: Tuple[str, ...] = (INTERN, JUNIOR, MID, SENIOR, LEAD, MANAGER)

# Years of experience a posting asks for at or below which it is entry-level. The
# scraper's `MAX_ENTRY_YEARS` is this constant, so the corpus filter and the
# scorer cannot disagree about what "2 years" means.
MAX_ENTRY_YEARS = 2
MAX_MID_YEARS = 4


def level_index(level: Optional[str]) -> Optional[int]:
    """Position of *level* in `LEVEL_ORDER`, or None for `unknown` and anything else.

    Stored JD profiles carry whatever string the detector returned when they
    were written, including `mid` from the old default. Every caller that
    reads one back goes through here so an unrecognised value degrades to
    "unknown" instead of raising.
    """
    try:
        return LEVEL_ORDER.index(level)  # type: ignore[arg-type]
    except ValueError:
        return None


# ── title patterns ────────────────────────────────────────────────────────────

# A noun that makes `lead` / `staff` / `principal` a job title rather than a
# verb, a headcount or a mathematical term ("principal component analysis").
_ROLE_NOUN = (
    r"(?:software|data|machine|ml|ai|applied|research|backend|back-end|frontend|"
    r"front-end|full[- ]?stack|platform|infrastructure|security|cloud|devops|"
    r"systems?|engineers?|engineering|developers?|scientists?|analysts?|"
    r"architects?|technologist|researchers?|consultants?|specialists?|product|"
    r"technical|analytics|quantitative|mobile|web|site)"
)

_INTERN_RE = re.compile(
    r"\bintern(?:ship)?\b|\bco-?op\b|\bapprentice(?:ship)?\b|\bsummer 20\d\d\b", re.I)

_MANAGER_RE = re.compile(
    r"\bmanager\b|\bdirector\b|\bvp\b|\bvice president\b|\bhead of\b|\bchief\b", re.I)

_LEAD_RE = re.compile(
    rf"\b(?:tech(?:nical)?|team|engineering)\s+lead\b"
    rf"|\b(?:lead|staff|principal)\s+(?:and\s+\w+\s+)?{_ROLE_NOUN}\b"
    rf"|\b{_ROLE_NOUN}\s*[,(-]?\s*(?:lead|staff|principal)\s*\)?$",
    re.I)

_BARE_LEAD_RE = re.compile(r"\b(?:lead|staff|principal)\b", re.I)

_SENIOR_RE = re.compile(
    r"\bsenior\b(?![- ]year)|\bsr\b\.?|\biii\b"
    rf"|\b{_ROLE_NOUN}\s*[,(-]?\s*senior\b", re.I)

_MID_RE = re.compile(r"\bmid[- ]level\b|\bii\b", re.I)

_JUNIOR_RE = re.compile(
    r"\bjunior\b|\bjr\b\.?|entry[- ]level|new[- ]?grad(?:uate)?s?\b|early[- ]career"
    r"|graduate program|\bassociate\b|\b(?:i|1)$", re.I)

# Markers that only make sense in a *posting* title. On a résumé, `fellow`,
# `university` and `campus` appear in roles that say nothing about seniority
# ("Data Science Fellow", "Teaching Assistant, University of X").
_POSTING_ONLY_SENIOR_RE = re.compile(r"\barchitect\b|\bdistinguished\b|\bfellow\b", re.I)
_POSTING_ONLY_JUNIOR_RE = re.compile(r"\buniversity\b|\bcampus\b", re.I)

# "Research Associate" / "Sales Associate" are job families, not a junior rung.
_NOT_A_RUNG_RE = re.compile(
    r"\b(?:research|teaching|sales|graduate|lab|laboratory|program|project|"
    r"administrative|marketing|retail|account)\s+associates?\b", re.I)

# A résumé line that opens with a past-tense or gerund verb is a bullet whose
# marker was lost ("Mentored junior engineers"), not a role line.
_VERBISH_START_RE = re.compile(r"^\W*\w+(?:ed|ing)\b", re.I)

_BULLET_RE = re.compile(r"^\s*(?:[-•●▪–—]|\*(?!\*)|\d+[.)])\s+")

_MAX_TITLE_WORDS = 12
_MAX_TITLE_CHARS = 110
# How far into a candidate's role line a leading modifier ("Senior", "Lead")
# may start and still describe the role: "Senior Data Scientist at X" yes,
# "Collaborated with senior stakeholders on ..." no.
_MAX_LEAD_WORDS = 3


def _is_title_like(line: str) -> bool:
    line = line.strip()
    if not line or len(line) > _MAX_TITLE_CHARS or len(line.split()) > _MAX_TITLE_WORDS:
        return False
    return not line.endswith((".", ";", "!", "?"))


def title_level(title: Optional[str], kind: str = "posting") -> Optional[str]:
    """Tier named by one title string, or None when it names none.

    *kind* says how far to trust the string. `"title"` is a job title known to
    be one (the scraper's field), so a bare `Lead`, `Staff` or `Principal` is a
    rung. `"posting"` is a line inferred to be a posting's heading, and
    `"candidate"` a résumé line inferred to be a role: both need a role noun
    beside `lead`/`staff`/`principal`, because the line may be a section heading
    ("Staff Benefits") or prose.

    Precedence is `intern`, then the highest of manager/lead/senior/mid, then
    `junior`. That ordering is the scraper's own: an `intern` marker keeps the
    posting in the corpus whatever else the title says ("Senior-Year Software
    Engineering Intern"), and a senior-family marker beats `associate`
    ("Associate Director").
    """
    s = (title or "").strip()
    if not s:
        return None
    candidate = kind == "candidate"
    if candidate and (_VERBISH_START_RE.match(s) or _BULLET_RE.match(s)):
        return None
    s = re.sub(r"[*_#>`]+", "", s)

    def hit(pattern: "re.Pattern", *, leading: bool = False) -> bool:
        m = pattern.search(s)
        if not m:
            return False
        if candidate and leading and len(s[: m.start()].split()) > _MAX_LEAD_WORDS:
            return False
        return True

    if hit(_INTERN_RE):
        return INTERN
    if hit(_MANAGER_RE):
        return MANAGER
    if hit(_LEAD_RE, leading=True) or (kind == "title" and hit(_BARE_LEAD_RE)):
        return LEAD
    if hit(_SENIOR_RE, leading=True) or (not candidate and hit(_POSTING_ONLY_SENIOR_RE)):
        return SENIOR
    if hit(_MID_RE, leading=True):
        return MID
    stripped = _NOT_A_RUNG_RE.sub(" ", s)
    m = _JUNIOR_RE.search(stripped)
    if m and not (candidate and len(stripped[: m.start()].split()) > _MAX_LEAD_WORDS):
        return JUNIOR
    if not candidate and _POSTING_ONLY_JUNIOR_RE.search(s):
        return JUNIOR
    return None


# ── body evidence (postings and candidate summaries) ──────────────────────────

# "3+ years of experience", "0-2 years experience", "experience: 5 years". Anchored
# to experience language so "founded 40 years ago" and "13,000+ people" do not
# read as a requirement. Group 1 is the lower bound of a range.
_YEARS_RE = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:(?:-|–|—|to)\s*\d{1,2}\s*\+?\s*)?years?\b[^.\n]{0,50}?experience"
    r"|experience[^.\n]{0,40}?(\d{1,2})\s*\+?\s*(?:(?:-|–|—|to)\s*\d{1,2}\s*\+?\s*)?years?\b",
    re.I)


def min_stated_years(text: Optional[str]) -> Optional[int]:
    """Smallest years-of-experience figure stated in *text*, or None.

    Lenient on purpose: "0-2 years required" beside "5 years preferred" is an
    entry posting, so the minimum is the right summary; the maximum would
    reject it on its own nice-to-have line.
    """
    values = [int(m.group(1) or m.group(2)) for m in _YEARS_RE.finditer(text or "")]
    return min(values) if values else None


def years_level(years: Optional[int]) -> Optional[str]:
    if years is None:
        return None
    if years <= MAX_ENTRY_YEARS:
        return JUNIOR
    if years <= MAX_MID_YEARS:
        return MID
    return SENIOR


# Self-referential phrases only. A bare `senior` or `junior` in a body usually
# describes the team ("mentor junior engineers", "senior leadership") and is
# therefore not evidence about the role.
#
# "We are hiring a senior engineer" is the one place a bare tier word does name
# the role: the sentence is about the opening. The hiring verb is what makes it
# so; "pair with a senior engineer" does not match.
def _hiring(tier_words: str) -> str:
    return (r"\b(?:hiring|seeking|looking\s+for|recruiting|searching\s+for)\s+"
            rf"(?:an?\s+)?(?:[\w-]+\s+)?(?:{tier_words})\b")


_BODY_PHRASES: List[Tuple[str, "re.Pattern"]] = [
    (INTERN, re.compile(
        r"\b(?:an?|this|the|our)\s+(?:[\w-]+\s+){0,2}intern(?:ship)?\b(?!\s+experience)"
        r"|\bintern(?:ship)?\s+(?:program|position|role|opportunity)\b", re.I)),
    (JUNIOR, re.compile(
        _hiring("junior|jr")
        + r"|\bentry[- ]level\b|\bnew[- ]?grad(?:uate)?s?\b|\brecent (?:college |university )?graduates?\b"
        r"|\bearly[- ]career\b|\bjunior[- ]level\b|\b(?:this|a) junior\b"
        r"|\b(?:no|without)\s+(?:prior\s+|previous\s+)?(?:work\s+|professional\s+|industry\s+)?"
        r"experience\s+(?:is\s+)?(?:required|needed|necessary)\b", re.I)),
    (MID, re.compile(r"\bmid[- ]level\b|\bmid[- ]career\b", re.I)),
    (SENIOR, re.compile(
        _hiring("senior|sr")
        + r"|\bsenior[- ]level\b|\bthis senior\b|\ba senior (?:role|position)\b", re.I)),
    (LEAD, re.compile(
        _hiring("staff|principal|lead")
        + r"|\b(?:staff|principal)[- ]level\b|\blead(?:ing)? a team of\b", re.I)),
    (MANAGER, re.compile(r"\bdirect reports\b|\bpeople manage(?:r|ment)\b|\bmanage a team of\b", re.I)),
]
_PHRASE_WEIGHT = 3
_YEARS_WEIGHT = 2


def _body_level(text: str) -> Optional[str]:
    """Tier the body states about the role itself, by weighted evidence."""
    score: Dict[str, int] = {}
    for tier, pattern in _BODY_PHRASES:
        n = len(pattern.findall(text))
        if n:
            score[tier] = score.get(tier, 0) + _PHRASE_WEIGHT * min(n, 2)
    ylevel = years_level(min_stated_years(text))
    if ylevel:
        score[ylevel] = score.get(ylevel, 0) + _YEARS_WEIGHT
    if not score:
        return None
    # Ties go to the lower tier: the weaker claim is the safer label.
    return max(score, key=lambda t: (score[t], -LEVEL_ORDER.index(t)))


def _lines(text: str) -> Iterable[str]:
    return (ln for ln in (text or "").splitlines() if ln.strip())


def detect_level(text: Optional[str], *, title: Optional[str] = None,
                 kind: str = "posting") -> str:
    """Seniority tier of a posting or a candidate, or `unknown`.

    *kind* is `"posting"` (a job description) or `"candidate"` (a résumé or
    profile). A posting is read from its title (the explicit *title*, else its
    first line) and then its self-referential body phrases and stated years; a
    candidate from its role lines, highest tier winning, then stated years.
    """
    text = text or ""
    if kind == "candidate":
        tiers = [t for t in (title_level(ln, "candidate") for ln in _lines(text)
                             if _is_title_like(ln)) if t]
        if tiers:
            return max(tiers, key=LEVEL_ORDER.index)
        return years_level(min_stated_years(text)) or UNKNOWN

    if title and title.strip():
        tier = title_level(title, "title")
    else:
        first = next(_lines(text), "")
        tier = title_level(first, "posting") if _is_title_like(first) else None
    return tier or _body_level(text) or UNKNOWN


def is_above_entry_title(title: Optional[str]) -> bool:
    """True when a known job title names a rung above entry level.

    The scraper's disqualifier: `mid` and up, which includes `Engineer II`.
    """
    tier = title_level(title, "title")
    return tier in (MID, SENIOR, LEAD, MANAGER)
