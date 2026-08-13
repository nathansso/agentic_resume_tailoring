"""Backfill job descriptions for postings that arrive as a title and a link.

Ported from the sibling `Job_Tracker` project's `jd_fetch.py`.

The community GitHub boards publish a title and a URL and nothing else, and
SmartRecruiters lists postings without bodies. Those are the sources that make
the corpus reach past big tech (issue #177), so half the corpus would otherwise
have no description to tailor against — and a posting with no body is never
admitted (`job_verification.is_verified` requires `description_fetched`).

Every posting links to a real listing, so the description is one request away.
Known ATS hosts have JSON endpoints; everything else gets its HTML stripped.

**Fetched once, ever.** Results live in a SQLite cache keyed by URL, so a run
only pays for postings it has never seen. Failures are cached too, with a retry
window — a dead link retried on every run is a slow leak in a job that already
takes minutes. The cache is a build artifact, not source: `eval/jd_dataset/` is
the checked-in output.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from job_sources import clean_html, unescape_fully  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "eval" / ".jd_cache.db"

# Ceiling on a stored body. Raised from 6,000 after `scripts/audit_jd_corpus.py`
# measured what 6,000 was doing: **48 of the 150 committed postings were cut at
# exactly that byte**, mid-word, and 14 more exceeded it — the feed adapters
# never applied the cap, so the corpus carried two different ceilings and the
# comment claiming they did was wrong. A user pasting a posting pastes all of
# it, and nothing downstream truncates: `job_analyzer` sends the whole body to
# the extractor and `jd_profile` reads requirements in source order (#121/#125),
# so a cut tail silently drops the end of the requirement list. 20,000 clears
# the longest real posting measured (8,821) with room, and still bounds a
# pathological page.
MAX_TEXT = 20000
WORKERS = 6                # politeness ceiling
TIMEOUT = 20
RETRY_AFTER = timedelta(days=14)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

# Removed with their contents before parsing: `get_text()` would otherwise
# return the body of a <script> as if it were prose.
_TAGS = re.compile(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>")


# ── cache ──────────────────────────────────────────────────────────────────────

def connect(path: "Path | None" = None) -> sqlite3.Connection:
    path = Path(path or CACHE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("""CREATE TABLE IF NOT EXISTS jd (
        url TEXT PRIMARY KEY, text TEXT, status TEXT, detail TEXT, fetched TEXT)""")
    return conn


def cached(conn: sqlite3.Connection, url: str) -> "tuple[str, str] | None":
    """(text, status) for a URL still worth trusting, else None to refetch."""
    row = conn.execute("SELECT text, status, fetched FROM jd WHERE url = ?",
                       (url,)).fetchone()
    if not row:
        return None
    text, status, fetched = row
    if status == "ok":
        return text or "", status
    # A failure is worth another try eventually — sites go down, and a posting
    # that 404s today may be a redirect that resolves next fortnight.
    try:
        when = datetime.fromisoformat(fetched)
    except (TypeError, ValueError):
        return None
    if datetime.now(timezone.utc) - when > RETRY_AFTER:
        return None
    return text or "", status


def store(conn: sqlite3.Connection, url: str, text: str, status: str,
          detail: str = "") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO jd (url, text, status, detail, fetched) VALUES (?,?,?,?,?)",
        (url, (text or "")[:MAX_TEXT], status, (detail or "")[:200],
         datetime.now(timezone.utc).isoformat(timespec="seconds")))


# ── html → text ────────────────────────────────────────────────────────────────

def strip_html(html: str) -> str:
    """HTML to readable text — the shape a user's paste has.

    Block-level boundaries become newlines and list items keep a `- ` marker, so
    a bulleted Requirements list survives as a list. Collapsing everything onto
    one line would erase the section boundaries the JD-profile extraction keys
    on (#121) and the ordinals #125 reads importance from.

    **Entities are unescaped before tags are stripped, and to a fixed point.**
    The previous implementation stripped tags with a regex first and unescaped
    afterwards, which is exactly backwards for a body that is escaped more than
    once — and posting bodies embedded in JSON usually are. `&lt;li&gt;`
    survived the tag pass untouched, and the entity pass then turned it into a
    literal `<li>` *in the finished text*: the posting's own markup rendered as
    prose, every block boundary gone, and 18 of the 150 committed postings
    carrying it (`scripts/audit_jd_corpus.py`). A naive `<[^>]+>` also stops at
    the first `>` inside an attribute value, which left fragments like
    `data-aria-level="1">` in the text — a parser handles what a regex cannot,
    so the parsing is delegated to `job_sources.clean_html` and there is now one
    HTML-to-text implementation in the repo instead of two that disagreed.
    """
    return clean_html(_TAGS.sub(" ", unescape_fully(html or "")))


def _get(url: str) -> str:
    resp = requests.get(url, timeout=TIMEOUT, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/json,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    resp.raise_for_status()
    return resp.text


# ── per-host handlers ──────────────────────────────────────────────────────────

def _lever(url: str) -> str:
    m = re.search(r"jobs\.lever\.co/([^/]+)/([0-9a-f-]{8,})", url, re.I)
    if not m:
        return ""
    d = json.loads(_get(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}"))
    lists = "\n".join(f"{s.get('text', '')}\n" + strip_html(s.get("content", ""))
                      for s in (d.get("lists") or []))
    return strip_html("\n".join([
        d.get("text", ""), d.get("descriptionPlain") or d.get("description", ""),
        lists, d.get("additionalPlain") or "",
    ]))


def _greenhouse(url: str) -> str:
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([^/?]+).*?(\d{6,})", url, re.I)
    if not m:
        return ""
    api = (f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}"
           f"/jobs/{m.group(2)}")
    return strip_html(json.loads(_get(api)).get("content", ""))


def _ashby(url: str) -> str:
    m = re.search(r"jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]{8,})", url, re.I)
    if not m:
        return ""
    data = json.loads(_get(
        f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}"))
    for j in data.get("jobs", []):
        if m.group(2) in (j.get("jobUrl") or ""):
            return strip_html(j.get("descriptionPlain")
                              or j.get("descriptionHtml", ""))
    return ""


def _smartrecruiters(url: str) -> str:
    m = re.search(r"smartrecruiters\.com/([^/]+)/(\d+)", url, re.I)
    if not m:
        return ""
    d = json.loads(_get(
        f"https://api.smartrecruiters.com/v1/companies/{m.group(1)}"
        f"/postings/{m.group(2)}"))
    sections = (d.get("jobAd") or {}).get("sections") or {}
    return strip_html("\n".join(
        str((sections.get(k) or {}).get("text") or "")
        for k in ("companyDescription", "jobDescription", "qualifications",
                  "additionalInformation")))


def _workable(url: str) -> str:
    m = re.search(r"apply\.workable\.com/([^/]+)/j/([A-Z0-9]+)", url, re.I)
    if not m:
        return ""
    d = json.loads(_get(
        f"https://apply.workable.com/api/v1/widget/accounts/{m.group(1)}"
        f"?details=true"))
    for j in d.get("jobs", []):
        if m.group(2).lower() in (j.get("url") or "").lower():
            return strip_html(j.get("description", ""))
    return ""


_HANDLERS = (
    ("jobs.lever.co", _lever),
    ("greenhouse.io", _greenhouse),
    ("jobs.ashbyhq.com", _ashby),
    ("smartrecruiters.com", _smartrecruiters),
    ("apply.workable.com", _workable),
)


def fetch_one(url: str) -> "tuple[str, str, str]":
    """(text, status, detail) for one posting URL. Never raises."""
    host = urllib.parse.urlparse(url).netloc.lower()
    try:
        for marker, handler in _HANDLERS:
            if marker in host:
                text = handler(url)
                if text:
                    return text[:MAX_TEXT], "ok", ""
                break                       # fall through to generic strip
        return strip_html(_get(url))[:MAX_TEXT], "ok", ""
    except Exception as e:
        return "", "error", f"{type(e).__name__}: {e}"


def fetch_missing(jobs: "list[dict]", conn: "sqlite3.Connection | None" = None,
                  verbose: bool = True) -> "list[dict]":
    """Fill in `text` for every posting that arrived without one.

    Mutates and returns `jobs`, setting `description_fetched` so verification
    can tell a body read from the live listing apart from one a feed supplied.
    Postings that already carry a body from their ATS feed are marked fetched
    without a request — the feed *is* the listing in that case.
    """
    own = conn is None
    conn = conn or connect()
    try:
        need = []
        for job in jobs:
            if job.get("text"):
                job["description_fetched"] = True
                continue
            url = job.get("url") or ""
            hit = cached(conn, url) if url else None
            if hit is not None:
                job["text"] = hit[0]
                job["description_fetched"] = hit[1] == "ok" and bool(hit[0])
            elif url:
                need.append(job)
            else:
                job["description_fetched"] = False

        if verbose:
            print(f"  descriptions: {len(need)} to fetch, "
                  f"{len(jobs) - len(need)} cached or already present")

        if need:
            with ThreadPoolExecutor(WORKERS) as ex:
                results = list(ex.map(lambda j: fetch_one(j["url"]), need))
            for job, (text, status, detail) in zip(need, results):
                store(conn, job["url"], text, status, detail)
                job["text"] = text
                job["description_fetched"] = status == "ok" and bool(text)
            conn.commit()
        return jobs
    finally:
        if own:
            conn.close()
