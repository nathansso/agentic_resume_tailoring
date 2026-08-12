"""Structural verification for benchmark job postings (issue #177).

Ported from the sibling `Job_Tracker` project's `legitimacy.py`, which was
written against a measured failure: on the first coverage-scored board only
**7 of the top 34 postings** were hosted on a real applicant-tracking system,
against 26% across the whole board. Content-farm listings name many skills by
construction, so any relevance score concentrates junk at the top — exactly
where it does the most damage.

That matters more for a benchmark than for a job board. A fabricated or
lead-generation listing is not a job description; tailoring against one measures
the pipeline's response to marketing copy, and the resulting number is
uninterpretable. So every posting in `eval/jd_dataset/` must carry evidence that
the employer exists and is actually hiring.

Every signal here is **structural, never reputational**. Nothing judges whether
a company is good, only whether there is evidence it exists:

* **Runs its own ATS.** Greenhouse, Ashby, Lever, Workday and the rest cost money
  and take setup, so a posting reachable on one comes from an employer paying for
  recruiting software. The single strongest signal available.
* **Corroborated across feeds.** Two independent aggregators carrying the same
  employer is weak evidence, but it is evidence. A listing in exactly one scraped
  feed and nowhere else is the shape lead-generation spam takes.
* **States a salary.** Optional on every aggregator, so stating one is a choice.
* **Trademark symbols in the company name.** "Prospect Equities®" is marketing
  copy pasted into a name field; real ATS integrations do not do this.

The corpus admission bar (`is_verified`) is deliberately stricter than the
tracker's, because a benchmark keeps its data for years while a job board's
churns weekly: **ATS-hosted, or corroborated by at least two independent feeds
with the description actually fetched from the live URL.** Measured against the
tracker's cache, that bar admits ~733 of 1,028 in-domain postings and every role
family clears 30.
"""
from __future__ import annotations

import re
import urllib.parse

# Hosts that mean the employer runs real recruiting software.
ATS_HOSTS = (
    "greenhouse.io", "ashbyhq.com", "lever.co", "myworkdayjobs.com",
    "workable.com", "smartrecruiters.com", "icims.com", "oraclecloud.com",
    "jobvite.com", "bamboohr.com", "recruitee.com", "teamtailor.com",
    "successfactors.com", "taleo.net", "paylocity.com", "adp.com",
    "breezy.hr", "rippling.com", "wellfound.com",
)

# Job boards rather than employers, so a link to one inside the JD body is not
# evidence that the company has a web presence.
BOARD_HOSTS = ATS_HOSTS + (
    "jobright.ai", "linkedin.com", "indeed.com", "glassdoor", "simplify.jobs",
    "ziprecruiter", "monster.com", "careerin.ai", "intern-list.com",
    "newgrad-jobs.com", "google.com", "youtube.com", "twitter.com", "x.com",
    "facebook.com", "instagram.com", "github.com",
)

UNPAID = re.compile(r"\bunpaid\b|\bno pay\b|\bvolunteer\b|\bnon[- ]?paid\b|"
                    r"\bstipend[- ]?free\b|\bequity[- ]only\b", re.I)

# A salary field that says nothing. Feeds use several spellings of "absent".
NO_SALARY = re.compile(r"^\s*(n/?a|none|not specified|tbd|-+|competitive|doe)?\s*$", re.I)

TRADEMARK = re.compile(r"[®™©]")

_URL = re.compile(r"https?://([\w.-]+)|\bwww\.([\w.-]+)", re.I)

# Scoring weights: structural evidence up, absence of it down.
W_ATS = 10
W_SALARY = 4
W_CORROBORATED = 4
W_UNVERIFIED = -8
W_TRADEMARK = -3

# Independent feeds required to admit a posting that is not ATS-hosted.
MIN_CORROBORATING_FEEDS = 2


def is_ats(url: str) -> bool:
    host = urllib.parse.urlparse(url or "").netloc.lower()
    return any(a in host for a in ATS_HOSTS)


def has_salary(job: dict) -> bool:
    s = str(job.get("salary") or "").strip()
    return bool(s) and not NO_SALARY.match(s) and not UNPAID.search(s)


def has_company_site(text: str) -> bool:
    """A link to somewhere that is not a job board — weak evidence the employer
    has a web presence the posting is willing to name."""
    for m in _URL.finditer(text or ""):
        host = (m.group(1) or m.group(2) or "").lower()
        if host and not any(b in host for b in BOARD_HOSTS):
            return True
    return False


def assess(job: dict, feed_count: int = 1) -> dict:
    """Structural credibility for one posting.

    `feed_count` is how many independent aggregators carry this employer, which
    the caller computes across the whole pull — it cannot be known from a single
    posting. Returns `{"score": int, "signals": [...], "block": str|None}`.
    """
    signals: list[str] = []
    score = 0

    salary_field = str(job.get("salary") or "")
    unpaid = bool(UNPAID.search(salary_field) or UNPAID.search(job.get("title") or ""))

    ats = is_ats(job.get("url", ""))
    if ats:
        score += W_ATS
        signals.append("own ATS")

    paid = has_salary(job)
    if paid:
        score += W_SALARY
        signals.append("salary stated")

    corroborated = feed_count >= MIN_CORROBORATING_FEEDS
    if corroborated:
        score += W_CORROBORATED
        signals.append(f"{feed_count} feeds")

    if not ats and not paid:
        score += W_UNVERIFIED
        signals.append("aggregator-only, no salary")

    if TRADEMARK.search(str(job.get("company") or "")):
        score += W_TRADEMARK
        signals.append("trademark in name")

    block = None
    if unpaid:
        block = "unpaid role"
    elif not ats and not paid and not corroborated and not has_company_site(
            job.get("description", "") or job.get("text", "")):
        block = "unverifiable employer"

    return {"score": score, "signals": signals, "block": block}


def is_verified(job: dict, feed_count: int = 1) -> bool:
    """Corpus admission bar: is there structural evidence behind this posting?

    Stricter than `assess`'s scoring because a benchmark corpus is kept for
    years. Requires the description to have been fetched from the live URL
    (`description_fetched`) *and* either an ATS host or independent
    corroboration. A blocked posting never qualifies however it scores.
    """
    if not job.get("description_fetched"):
        return False
    if assess(job, feed_count)["block"]:
        return False
    return is_ats(job.get("url", "")) or feed_count >= MIN_CORROBORATING_FEEDS


def feed_counts(jobs: list) -> dict:
    """Company → how many distinct feeds carry it.

    Keyed on the same normalized company string the dedup uses, so two spellings
    of one employer count once. Sources are split on "/" because a feed names
    itself `simplify-2027/Internship`, and the section after the slash is a
    category within one feed, not an independent witness.
    """
    seen: dict = {}
    for j in jobs:
        key = company_key(j)
        if key:
            seen.setdefault(key, set()).add(str(j.get("source") or "").split("/")[0])
    return {k: len(v) for k, v in seen.items()}


def company_key(job: dict) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(job.get("company") or "").lower())
