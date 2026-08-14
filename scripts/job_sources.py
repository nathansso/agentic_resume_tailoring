"""Job-posting source adapters for the benchmark corpus (issue #177).

Ported from the sibling `Job_Tracker` project (`sources.py`, `aggregators.py`),
which had already solved the problem #177 ran into: ART's own scraper knew five
Greenhouse tokens and two Lever tokens, and across ~110 hand-guessed tokens on
three ATS platforms it could only reach **6 entry-level AI-engineering postings
and 5 data-science ones**. The tracker, pointed at the same population on the
same day, held 1,028.

The difference is not effort, it is architecture, and the tracker's own note
states it: *"a hand-curated list of ATS tokens can never be comprehensive.
Probing 74 guessed Greenhouse tokens found 8 live ones — board tokens rarely
match company names, so guessing does not scale."* Independently reproduced
here: 32 of 80 guessed tokens 404'd.

**The aggregators solve it.** Community-curated GitHub boards list early-career
postings whose apply links point at the employer's *real* ATS board. So the
feeds themselves say which tokens exist, across pharma, defence, retail and
utilities as readily as tech. Harvest the tokens from the links
(`discover_ats_tokens`), then read those boards in full — which also surfaces
roles the aggregators never listed.

Two source tiers, deliberately:

* **ATS adapters** (Greenhouse, Lever, Ashby, SmartRecruiters, Workable) return
  a full description in the feed. These are the strongest verification signal —
  an employer paying for recruiting software — see `job_verification.py`.
* **Aggregator adapters** (the GitHub boards) return a title and a link and
  nothing else. Their descriptions must be fetched separately
  (`job_descriptions.py`), and a posting whose body was never fetched is never
  admitted to the corpus.

Every adapter yields the same raw shape and swallows its own failures, so one
dead feed cannot take down a run:

    {company, title, location, url, posted, salary, text, source}
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}
TIMEOUT = 40
WORKERS = 6  # politeness ceiling, matching the source project


def _text(url: str, headers: "dict | None" = None) -> str:
    resp = requests.get(url, headers={**UA, **(headers or {})}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _json(url: str, headers: "dict | None" = None):
    resp = requests.get(url, headers={**UA, **(headers or {})}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# Characters that survive an unescape as invisible junk: non-breaking and
# narrow spaces, zero-width joiners, the BOM. They are not what a paste looks
# like and they split tokens for anything counting words, so they are folded to
# an ordinary space here rather than left for each consumer to rediscover.
_INVISIBLE = re.compile(r"[   ​‌‍⁠﻿‎‏]")


# Bodies embedded in JSON are routinely escaped more than once — Greenhouse
# always is, and SmartRecruiters' section text can be too. A single unescape
# leaves the second layer behind, which surfaces as `&#xa0;` and `&amp;` in the
# finished text and, when the inner layer is markup, as literal `<li>`. Bounded
# because a body containing a literal "&amp;amp;" would otherwise oscillate.
_MAX_UNESCAPE_PASSES = 3


def unescape_fully(raw: str) -> str:
    import html as _html

    text = raw or ""
    for _ in range(_MAX_UNESCAPE_PASSES):
        once = _html.unescape(text)
        if once == text:
            break
        text = once
    return text


def clean_html(raw: str) -> str:
    """Job-board APIs return HTML (Greenhouse double-escapes it). → plain text."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(unescape_fully(raw), "html.parser")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
    for block in soup.find_all(["p", "div", "br", "h1", "h2", "h3", "h4", "ul"]):
        block.insert_before("\n")
    text = _INVISIBLE.sub(" ", soup.get_text())
    text = re.sub(r"[ \t\r]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ── community GitHub boards ───────────────────────────────────────────────────
#
# 2026-cycle boards are deliberately excluded: their rows carry only a title, so
# there is no JD text for the level filter to read, and a 2026 new-grad list
# would sail through and dilute the corpus rather than being caught.
#
# (url, kind, source label)
GITHUB_BOARDS = [
    ("https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/NEW_GRAD_USA.md",
     "New Grad", "speedyapply-swe"),
    ("https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md",
     "Internship", "speedyapply-swe"),
    ("https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/NEW_GRAD_USA.md",
     "New Grad", "speedyapply-ai"),
    ("https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/README.md",
     "Internship", "speedyapply-ai"),
    ("https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/README.md",
     "Internship", "vanshb03-2027"),
    ("https://raw.githubusercontent.com/vanshb03/New-Grad-2027/main/README.md",
     "New Grad", "vanshb03-newgrad"),
    ("https://raw.githubusercontent.com/cvrve/New-Grad/main/README.md",
     "New Grad", "cvrve-newgrad"),
    ("https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md",
     "New Grad", "simplify-newgrad"),
    ("https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md",
     "Internship", "simplify-2027"),
]

_COMPANY_LINK = re.compile(r"<strong>\s*<a[^>]*>([^<]+)</a>\s*</strong>", re.S)
_COMPANY_HTML = re.compile(r"<strong>([^<]+)</strong>")
_COMPANY_MD = re.compile(r"\*\*([^*]+)\*\*")
_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_HREF = re.compile(r'href="([^"]+)"')

# Boards age rows as "0d", "3w", "6mo", "1y". SimplifyJobs dates most of its rows
# in months, so handling only "Nd" silently drops the bulk of the widest list.
_AGE = re.compile(r"^(\d+)\s*(d|w|mo|m|y)$", re.I)
_AGE_UNIT_DAYS = {"d": 1, "w": 7, "mo": 30, "m": 30, "y": 365}
_MON_DAY = re.compile(r"^([A-Z][a-z]{2})\s+(\d{1,2})$")
_SALARY = re.compile(r"^\$")
_CARRY = "↳"
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def parse_posted(cell: str) -> "str | None":
    """Boards date rows either as an age ("7d") or a bare "Jul 28" with no year."""
    cell = (cell or "").strip()
    m = _AGE.match(cell)
    if m:
        days = int(m.group(1)) * _AGE_UNIT_DAYS[m.group(2).lower()]
        return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    m = _MON_DAY.match(cell)
    if m:
        mon, day = _MONTHS.get(m.group(1)), int(m.group(2))
        if not mon:
            return None
        today = date.today()
        try:
            d = date(today.year, mon, day)
        except ValueError:
            return None
        # No year in the cell, so assume the current one — but a posting cannot
        # be dated in the future, so anything ahead of today is last year's.
        # (One day of slack absorbs board/local timezone disagreement.)
        if (d - today).days > 1:
            try:
                d = date(today.year - 1, mon, day)
            except ValueError:
                return None
        return d.isoformat()
    return None


def _is_table_chrome(cells: "list[str]") -> bool:
    """The header row and the |---|---| separator under it.

    Tested on the parsed cells, not the raw line: matching `---` anywhere in the
    line drops every posting whose apply URL contains a triple dash (Workday
    builds them that way), and matching "Company" drops every employer with
    "Company" in its name — Chicago Trading Company among them.
    """
    if not cells:
        return True
    if cells[0].strip().lower() in ("company", "name"):
        return True
    return all(re.fullmatch(r"[:\-\s]*", c) for c in cells)


def parse_board_row(cells: "list[str]", kind: str, source: str,
                    carry: str) -> "dict | None":
    """One markdown/HTML table row → a raw posting, or None if unusable."""
    if len(cells) < 5 or _is_table_chrome(cells):
        return None

    raw_company = cells[0]
    if raw_company.startswith(_CARRY):
        company = carry          # "↳" repeats the employer above
    else:
        m = (_COMPANY_LINK.search(raw_company) or _COMPANY_HTML.search(raw_company)
             or _COMPANY_MD.search(raw_company))
        company = (m.group(1) if m else raw_company).strip()
    if not company:
        return None

    posted = parse_posted(cells[-1])
    if posted is None:
        return None
    href = _HREF.search(cells[-2])
    if not href:
        return None

    salary = ""
    if len(cells) >= 6 and _SALARY.match(cells[-3]):
        salary, loc = cells[-3], cells[-4]
    else:
        loc = cells[-3]

    title = cells[1]
    if "🔒" in title:                      # application already closed
        return None
    title = re.sub(r"[🛂🇺🇸🔒]", "", title).strip()
    if not title:
        return None

    return {
        "company": company,
        "title": title,
        "location": re.sub(r"</?br\s*/?>", "; ", loc).strip(),
        "url": href.group(1).split("?utm_source=")[0].strip(),
        "posted": posted,
        "salary": salary,
        "text": "",              # aggregators carry no body; fetched separately
        "source": f"{source}/{kind}",
    }


def _cell_rows(body: str) -> "list[list[str]]":
    """Cells per row, from a markdown table or an HTML one.

    Both formats carry the same columns in the same order, so unifying here
    means one row parser and no second implementation to keep in sync.
    """
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")]
            for ln in body.split("\n") if ln.startswith("|")]
    if rows:
        return rows
    return [[c.strip() for c in _TD.findall(tr)] for tr in _TR.findall(body)]


def fetch_github_boards(verbose: bool = True) -> "list[dict]":
    def one(url: str, kind: str, source: str) -> "list[dict]":
        try:
            body = _text(url)
        except Exception as e:
            if verbose:
                print(f"  {source} [{kind}] failed: {str(e)[:60]}")
            return []
        found, carry = [], ""
        for cells in _cell_rows(body):
            row = parse_board_row(cells, kind, source, carry)
            if not row:
                continue
            carry = row["company"]
            found.append(row)
        if verbose:
            print(f"  {source} [{kind}]: {len(found)} postings")
        return found

    out: "list[dict]" = []
    with ThreadPoolExecutor(WORKERS) as ex:
        for rows in ex.map(lambda b: one(*b), GITHUB_BOARDS):
            out.extend(rows)
    return out


# ── ATS token discovery ───────────────────────────────────────────────────────

BOARD_PATTERNS = {
    "greenhouse": re.compile(
        r"(?:job-boards|boards)\.greenhouse\.io/(?:embed/job_app\?for=)?([a-z0-9_-]+)", re.I),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_.-]+)", re.I),
    "lever": re.compile(r"jobs\.lever\.co/([a-z0-9_.-]+)", re.I),
    "smartrecruiters": re.compile(r"jobs\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I),
    "workable": re.compile(r"apply\.workable\.com/([a-z0-9_-]+)/", re.I),
}

# Path segments that are part of the URL scheme rather than a board token.
_NOT_A_TOKEN = {"embed", "j", "api", "jobs", "job", "en", "www", "apply", "widget"}


def discover_ats_tokens(jobs: "list[dict]") -> "dict[str, list[str]]":
    """Harvest real ATS board tokens from aggregator apply links.

    This is the mechanism that makes coverage scale. Tokens are returned sorted
    so a rescrape reads boards in a stable order (#158/#171 determinism).
    """
    found: "dict[str, set]" = {k: set() for k in BOARD_PATTERNS}
    for job in jobs:
        url = job.get("url") or ""
        for platform, pattern in BOARD_PATTERNS.items():
            m = pattern.search(url)
            if m:
                token = m.group(1).lower()
                if token not in _NOT_A_TOKEN:
                    found[platform].add(token)
    return {k: sorted(v) for k, v in found.items()}


# ── ATS adapters ──────────────────────────────────────────────────────────────

def fetch_greenhouse(token: str) -> "list[dict]":
    data = _json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    return [{
        "company": (j.get("company_name") or token).strip(),
        "title": j.get("title", ""),
        "location": (j.get("location") or {}).get("name", ""),
        "url": j.get("absolute_url", ""),
        "posted": (j.get("updated_at") or "")[:10] or None,
        "salary": "",
        "text": clean_html(j.get("content", "")),
        "source": "greenhouse",
    } for j in data.get("jobs", [])]


def fetch_lever(token: str) -> "list[dict]":
    out = []
    for j in _json(f"https://api.lever.co/v0/postings/{token}?mode=json"):
        parts = [j.get("descriptionPlain") or clean_html(j.get("description", ""))]
        for lst in j.get("lists", []):
            parts.append(lst.get("text", ""))
            parts.append(clean_html(lst.get("content", "")))
        rng = j.get("salaryRange") or {}
        salary = (f"{rng.get('currency', '')}{rng.get('min')}-{rng.get('max')}"
                  if rng.get("min") else "")
        out.append({
            "company": token,
            "title": j.get("text", ""),
            "location": (j.get("categories") or {}).get("location", ""),
            "url": j.get("hostedUrl", ""),
            "posted": (j.get("createdAt") and
                       datetime.fromtimestamp(j["createdAt"] / 1000,
                                              timezone.utc).date().isoformat()),
            "salary": salary,
            "text": "\n\n".join(p for p in parts if p).strip(),
            "source": "lever",
        })
    return out


def fetch_ashby(token: str) -> "list[dict]":
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"
    data = _json(url)
    return [{
        "company": j.get("organizationName") or token,
        "title": j.get("title", ""),
        "location": j.get("location", ""),
        "url": j.get("jobUrl", ""),
        "posted": (j.get("publishedAt") or "")[:10] or None,
        "salary": str((j.get("compensation") or {}).get("summaryComponents") or ""),
        "text": j.get("descriptionPlain") or clean_html(j.get("descriptionHtml", "")),
        "source": "ashby",
    } for j in data.get("jobs", [])]


def fetch_smartrecruiters(token: str) -> "list[dict]":
    """SmartRecruiters lists postings without bodies; the body is a second call.

    Left unfetched here — `job_descriptions.fetch_missing` handles it through the
    same cache every aggregator posting uses, so one politeness budget covers all
    of them.
    """
    data = _json(f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100")
    return [{
        "company": (j.get("company") or {}).get("name") or token,
        "title": j.get("name", ""),
        "location": ", ".join(filter(None, [
            (j.get("location") or {}).get("city"),
            (j.get("location") or {}).get("region")])),
        "url": f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}",
        "posted": (j.get("releasedDate") or "")[:10] or None,
        "salary": "",
        "text": "",
        "source": "smartrecruiters",
    } for j in data.get("content", [])]


def fetch_workable(token: str) -> "list[dict]":
    data = _json(f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true")
    return [{
        "company": data.get("name") or token,
        "title": j.get("title", ""),
        "location": j.get("location", {}).get("location_str", "") if isinstance(
            j.get("location"), dict) else str(j.get("location") or ""),
        "url": j.get("url") or j.get("application_url") or "",
        "posted": (j.get("published_on") or "")[:10] or None,
        "salary": "",
        "text": clean_html(j.get("description", "")),
        "source": "workable",
    } for j in data.get("jobs", [])]


ATS_ADAPTERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workable": fetch_workable,
}


def fetch_ats(tokens: "dict[str, list[str]]", verbose: bool = True) -> "list[dict]":
    """Read every discovered board. A token that 404s costs one wasted request.

    Platforms and tokens are both iterated in sorted order so the pull is
    reproducible.
    """
    jobs: "list[tuple[str, str]]" = [
        (platform, token)
        for platform in sorted(tokens)
        for token in sorted(tokens[platform])
        if platform in ATS_ADAPTERS
    ]

    def one(pair):
        platform, token = pair
        try:
            return ATS_ADAPTERS[platform](token)
        except Exception:
            return []          # dead token: silent, by design

    out: "list[dict]" = []
    with ThreadPoolExecutor(WORKERS) as ex:
        for rows in ex.map(one, jobs):
            out.extend(rows)
    if verbose:
        print(f"  ATS boards: {len(jobs)} tokens → {len(out)} postings")
    return out
