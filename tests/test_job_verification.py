"""Structural verification of benchmark postings (issue #177).

Pins the corpus admission bar. The signals are all structural — an ATS host,
corroboration across independent feeds, a stated salary — so every case here is
about *evidence the employer exists*, never about whether the employer is good.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from job_verification import (  # noqa: E402
    MIN_CORROBORATING_FEEDS,
    assess,
    company_key,
    feed_counts,
    has_company_site,
    is_ats,
    is_verified,
)

_ATS = "https://job-boards.greenhouse.io/scaleai/jobs/4703343005"
_AGG = "https://jobright.ai/jobs/info/abc123"


def _job(url=_ATS, company="Scale AI", title="AI Engineer Intern",
         description="We are hiring. See https://scale.com for details.",
         fetched=True, **kw):
    return {"url": url, "company": company, "title": title,
            "description": description, "description_fetched": fetched, **kw}


# ── ATS detection ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://job-boards.greenhouse.io/x/jobs/1",
    "https://jobs.lever.co/x/abc",
    "https://jobs.ashbyhq.com/x/abc",
    "https://nvidia.wd5.myworkdayjobs.com/en-US/site/job/1",
    "https://jobs.smartrecruiters.com/x/1",
    "https://apply.workable.com/x/j/ABC/",
])
def test_real_ats_hosts_are_recognized(url):
    assert is_ats(url)


@pytest.mark.parametrize("url", [
    "https://jobright.ai/jobs/info/1",
    "https://www.linkedin.com/jobs/view/1",
    "https://example.com/careers/1",
    "",
])
def test_non_ats_hosts_are_not(url):
    assert not is_ats(url)


# ── admission bar ──────────────────────────────────────────────────────────────

def test_ats_hosted_and_fetched_is_admitted():
    assert is_verified(_job(), feed_count=1)


def test_aggregator_posting_needs_corroboration():
    job = _job(url=_AGG)
    assert not is_verified(job, feed_count=1)
    assert is_verified(job, feed_count=MIN_CORROBORATING_FEEDS)


def test_an_unfetched_description_is_never_admitted():
    """A title and a link is not a job description.

    The aggregator feeds publish title + URL only; admitting one without the
    fetched body would put an empty description into the corpus.
    """
    assert not is_verified(_job(fetched=False), feed_count=9)


def test_unpaid_roles_are_blocked_regardless_of_evidence():
    job = _job(title="Data Science Intern (Unpaid)")
    assert assess(job)["block"] == "unpaid role"
    assert not is_verified(job, feed_count=9)


def test_an_employer_with_no_evidence_at_all_is_blocked():
    job = _job(url=_AGG, description="Great opportunity! Apply now.", )
    result = assess(job, feed_count=1)
    assert result["block"] == "unverifiable employer"
    assert not is_verified(job, feed_count=1)


def test_a_company_site_in_the_body_prevents_the_hard_block():
    """Weak evidence, but evidence — it stops the block without admitting it."""
    job = _job(url=_AGG, description="Apply at https://realcompany.com/careers")
    assert assess(job, feed_count=1)["block"] is None
    assert not is_verified(job, feed_count=1)  # still needs ATS or corroboration


# ── scoring signals ────────────────────────────────────────────────────────────

def test_ats_is_the_strongest_single_signal():
    ats = assess(_job())["score"]
    agg = assess(_job(url=_AGG))["score"]
    assert ats > agg


def test_salary_and_corroboration_add_signals():
    result = assess(_job(salary="$45/hr"), feed_count=3)
    assert "own ATS" in result["signals"]
    assert "salary stated" in result["signals"]
    assert "3 feeds" in result["signals"]


@pytest.mark.parametrize("salary", ["", "N/A", "Competitive", "TBD", "none", "-"])
def test_empty_salary_spellings_do_not_count_as_stated(salary):
    assert "salary stated" not in assess(_job(salary=salary))["signals"]


def test_trademark_in_company_name_is_penalized():
    plain = assess(_job(company="Prospect Equities"))["score"]
    marked = assess(_job(company="Prospect Equities®"))
    assert marked["score"] < plain
    assert "trademark in name" in marked["signals"]


def test_company_site_ignores_job_board_links():
    assert not has_company_site("Apply on https://www.linkedin.com/jobs/view/1")
    assert has_company_site("More at https://realcompany.io/about")


# ── corroboration counting ─────────────────────────────────────────────────────

def test_feed_counts_treats_categories_of_one_feed_as_one_witness():
    """`simplify-2027/Internship` and `simplify-2027/New Grad` are one source.

    Counting them as two would manufacture corroboration out of a single
    aggregator's own section headings.
    """
    jobs = [
        {"company": "Acme", "source": "simplify-2027/Internship"},
        {"company": "Acme", "source": "simplify-2027/New Grad"},
    ]
    assert feed_counts(jobs)["acme"] == 1


def test_feed_counts_counts_independent_feeds():
    jobs = [
        {"company": "Acme", "source": "simplify-2027/Internship"},
        {"company": "Acme", "source": "vanshb03-2027/Internship"},
        {"company": "Acme", "source": "greenhouse"},
    ]
    assert feed_counts(jobs)["acme"] == 3


def test_company_key_normalizes_spellings():
    assert company_key({"company": "Scale AI"}) == company_key({"company": "scale-ai"})
    assert company_key({"company": ""}) == ""
