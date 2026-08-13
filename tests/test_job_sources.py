"""Aggregator parsing, ATS token discovery, and JD text fetch (issue #177).

All offline: the row parsers, the token harvester and the description cache are
pure or cache-backed, so none of this touches the network. The adapters that do
are exercised by the corpus itself.

The parsers carry quirks that were each paid for in the source project, and the
tests name them so a "cleanup" cannot quietly undo one: the `↳` carry row, emoji
status flags, an optional salary column, month-only dates with no year, and
table chrome that must not be detected by matching `---` against the raw line.
"""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import job_descriptions  # noqa: E402
from job_sources import (  # noqa: E402
    _cell_rows,
    _is_table_chrome,
    discover_ats_tokens,
    parse_board_row,
    parse_posted,
)


# ── posted-date parsing ────────────────────────────────────────────────────────

@pytest.mark.parametrize("cell,days", [("0d", 0), ("7d", 7), ("3w", 21),
                                       ("6mo", 180), ("2m", 60), ("1y", 365)])
def test_age_cells_resolve_to_dates(cell, days):
    expected = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    assert parse_posted(cell) == expected


def test_month_units_are_handled_not_just_days():
    """SimplifyJobs dates most of its rows in months.

    Handling only "Nd" silently dropped the bulk of the widest board in the
    source project.
    """
    assert parse_posted("6mo") is not None


def test_bare_month_day_assumes_this_year_unless_that_is_the_future():
    today = date.today()
    past = date(today.year, 1, 15)
    assert parse_posted("Jan 15") == past.isoformat() or today.month == 1
    # A date ahead of today has to be last year's: postings are not predated.
    future = today + timedelta(days=60)
    parsed = parse_posted(future.strftime("%b %-d") if sys.platform != "win32"
                          else future.strftime("%b ") + str(future.day))
    assert parsed is not None and parsed <= today.isoformat()


@pytest.mark.parametrize("cell", ["", "  ", "yesterday", "2026-01-01", "Foo 99"])
def test_unparseable_dates_return_none(cell):
    assert parse_posted(cell) is None


# ── table chrome ───────────────────────────────────────────────────────────────

def test_header_and_separator_rows_are_chrome():
    assert _is_table_chrome(["Company", "Role", "Location", "Link", "Age"])
    assert _is_table_chrome(["---", ":---", "---"])
    assert _is_table_chrome([])


def test_a_company_named_company_is_not_chrome():
    """Matching "Company" anywhere dropped Chicago Trading Company."""
    cells = ["**Chicago Trading Company**", "SWE Intern", "Chicago",
             '<a href="https://x.co/1">Apply</a>', "3d"]
    assert not _is_table_chrome(cells)
    assert parse_board_row(cells, "Internship", "test", "") is not None


def test_a_triple_dash_in_the_apply_url_is_not_chrome():
    """Workday builds URLs with triple dashes; matching `---` on the raw line
    dropped every one of those postings."""
    cells = ["**Acme**", "Data Engineer I", "NY",
             '<a href="https://x.wd5.myworkdayjobs.com/job/A---B---C">Apply</a>', "5d"]
    assert parse_board_row(cells, "New Grad", "test", "") is not None


# ── row parsing ────────────────────────────────────────────────────────────────

def _cells(company="**Acme**", title="Software Engineer Intern", loc="NYC",
           link='<a href="https://job-boards.greenhouse.io/acme/jobs/1">Apply</a>',
           age="3d", salary=None):
    return [company, title, loc] + ([salary] if salary else []) + [link, age]


def test_a_plain_row_parses():
    row = parse_board_row(_cells(), "Internship", "simplify-2027", "")
    assert row["company"] == "Acme"
    assert row["title"] == "Software Engineer Intern"
    assert row["source"] == "simplify-2027/Internship"
    assert row["url"].endswith("/jobs/1")
    assert row["text"] == ""          # aggregators carry no body


def test_carry_rows_inherit_the_company_above():
    """`↳` means "same employer as the previous row"."""
    row = parse_board_row(_cells(company="↳"), "Internship", "s", "Previous Co")
    assert row["company"] == "Previous Co"


def test_closed_postings_are_dropped():
    assert parse_board_row(_cells(title="SWE Intern 🔒"), "Internship", "s", "") is None


def test_status_emoji_are_stripped_from_the_title():
    row = parse_board_row(_cells(title="SWE Intern 🛂🇺🇸"), "Internship", "s", "")
    assert row["title"] == "SWE Intern"


def test_optional_salary_column_is_detected_by_its_dollar_sign():
    row = parse_board_row(_cells(salary="$45/hr"), "Internship", "s", "")
    assert row["salary"] == "$45/hr"
    assert row["location"] == "NYC"


def test_utm_tracking_is_stripped_from_the_apply_url():
    link = '<a href="https://jobs.lever.co/acme/abc?utm_source=simplify">Apply</a>'
    row = parse_board_row(_cells(link=link), "Internship", "s", "")
    assert row["url"] == "https://jobs.lever.co/acme/abc"


def test_rows_without_a_link_or_a_date_are_dropped():
    assert parse_board_row(_cells(link="no link here"), "Internship", "s", "") is None
    assert parse_board_row(_cells(age="soon"), "Internship", "s", "") is None


def test_markdown_and_html_tables_parse_to_the_same_cells():
    md = "| **Acme** | SWE | NYC | link | 3d |"
    html = "<tr><td>**Acme**</td><td>SWE</td><td>NYC</td><td>link</td><td>3d</td></tr>"
    assert _cell_rows(md) == _cell_rows(html)


# ── ATS token discovery ────────────────────────────────────────────────────────

def test_tokens_are_harvested_from_apply_links():
    jobs = [
        {"url": "https://job-boards.greenhouse.io/scaleai/jobs/47"},
        {"url": "https://boards.greenhouse.io/acme/jobs/1"},
        {"url": "https://jobs.lever.co/plaid/abc"},
        {"url": "https://jobs.ashbyhq.com/ramp/xyz"},
        {"url": "https://jobs.smartrecruiters.com/AbbVie/123"},
        {"url": "https://apply.workable.com/foo/j/ABC/"},
    ]
    tokens = discover_ats_tokens(jobs)
    assert tokens["greenhouse"] == ["acme", "scaleai"]
    assert tokens["lever"] == ["plaid"]
    assert tokens["ashby"] == ["ramp"]
    assert tokens["smartrecruiters"] == ["abbvie"]
    assert tokens["workable"] == ["foo"]


def test_url_scheme_segments_are_not_mistaken_for_tokens():
    tokens = discover_ats_tokens([
        {"url": "https://boards.greenhouse.io/embed/job_app?for=realco"},
    ])
    assert "embed" not in tokens["greenhouse"]
    assert tokens["greenhouse"] == ["realco"]


def test_discovery_output_is_sorted_for_reproducibility():
    """Board read order must not depend on feed order (#158/#171)."""
    urls = [{"url": f"https://jobs.lever.co/{t}/x"} for t in ("zeta", "alpha", "mid")]
    assert discover_ats_tokens(urls)["lever"] == ["alpha", "mid", "zeta"]
    assert discover_ats_tokens(list(reversed(urls)))["lever"] == ["alpha", "mid", "zeta"]


def test_non_ats_links_yield_nothing():
    tokens = discover_ats_tokens([{"url": "https://jobright.ai/jobs/info/1"}])
    assert all(v == [] for v in tokens.values())


# ── description fetch + cache ──────────────────────────────────────────────────

def test_strip_html_keeps_bullet_lines_separate():
    """Block closers become newlines first.

    Collapsing a Requirements list onto one line would erase the section
    boundaries #121's JD-profile extraction keys on, and defeat the
    years-of-experience gate, which reads requirement bullets.
    """
    text = job_descriptions.strip_html(
        "<ul><li>3+ years of Python</li><li>BS in CS</li></ul>")
    assert "3+ years of Python" in text
    assert text.count("\n") >= 1


def test_strip_html_drops_script_and_style_bodies():
    text = job_descriptions.strip_html(
        "<script>var x=1;</script><style>.a{}</style><p>Real content</p>")
    assert "Real content" in text
    assert "var x" not in text and ".a{" not in text


def test_strip_html_unescapes_before_stripping_tags():
    """A double-escaped body must not emerge as literal markup.

    Bodies embedded in JSON are routinely escaped more than once — Greenhouse
    always is. Stripping tags first leaves `&lt;li&gt;` untouched and a later
    entity pass then turns it into a literal `<li>` in the finished text: the
    posting's own markup rendered as prose, with every block boundary gone.
    18 of the 150 committed postings carry exactly that
    (`scripts/audit_jd_corpus.py`).
    """
    text = job_descriptions.strip_html(
        "&lt;p&gt;We need &lt;strong&gt;Python&lt;/strong&gt;&lt;/p&gt;"
        "&lt;ul&gt;&lt;li&gt;3 years SQL&lt;/li&gt;&lt;li&gt;Airflow&lt;/li&gt;&lt;/ul&gt;")
    assert "<" not in text and ">" not in text
    assert "We need Python" in text
    assert "- 3 years SQL" in text and "- Airflow" in text


def test_strip_html_survives_an_angle_bracket_inside_an_attribute():
    """`<[^>]+>` stops at the first `>` inside an attribute value and leaves the
    remainder of the tag in the text — the source of fragments like
    `data-aria-level="1">` in the committed corpus. A parser handles it."""
    text = job_descriptions.strip_html(
        '<span data-x="a>b" data-aria-level="1">Familiarity with agile</span>')
    assert text == "Familiarity with agile"


def test_strip_html_renders_apostrophe_entities_as_apostrophes():
    """`&rsquo;` fell through to the catch-all entity rule and became a space,
    turning "today's" into "today s" — invented word boundaries in the text
    every keyword metric counts over."""
    text = job_descriptions.strip_html("<p>you&#39;ll ship today&rsquo;s work</p>")
    assert "you'll" in text
    assert "today’s" in text
    assert " s " not in text


def test_the_text_ceiling_clears_the_longest_real_posting():
    """6,000 cut 48 of the 150 committed postings mid-word, and the feed
    adapters never applied it at all — the corpus carried two ceilings. Nothing
    downstream truncates, so a cut tail silently drops the end of the
    requirement list `jd_profile` reads in source order (#121/#125)."""
    assert job_descriptions.MAX_TEXT >= 20000


def test_a_posting_that_already_has_a_body_is_marked_fetched_without_a_request(tmp_path):
    conn = job_descriptions.connect(tmp_path / "c.db")
    jobs = [{"url": "https://example.com/1", "text": "Full description here."}]
    job_descriptions.fetch_missing(jobs, conn=conn, verbose=False)
    assert jobs[0]["description_fetched"] is True
    conn.close()


def test_a_cached_body_is_reused(tmp_path):
    conn = job_descriptions.connect(tmp_path / "c.db")
    job_descriptions.store(conn, "https://example.com/2", "Cached body", "ok")
    conn.commit()
    jobs = [{"url": "https://example.com/2", "text": ""}]
    job_descriptions.fetch_missing(jobs, conn=conn, verbose=False)
    assert jobs[0]["text"] == "Cached body"
    assert jobs[0]["description_fetched"] is True
    conn.close()


def test_a_cached_failure_is_not_refetched_inside_the_retry_window(tmp_path):
    conn = job_descriptions.connect(tmp_path / "c.db")
    job_descriptions.store(conn, "https://example.com/3", "", "error", "404")
    conn.commit()
    assert job_descriptions.cached(conn, "https://example.com/3") == ("", "error")
    conn.close()


def test_a_stale_failure_is_retried(tmp_path):
    """A dead link may be a redirect that resolves next fortnight."""
    conn = job_descriptions.connect(tmp_path / "c.db")
    old = (datetime.now(timezone.utc)
           - job_descriptions.RETRY_AFTER - timedelta(days=1)).isoformat()
    conn.execute("INSERT INTO jd VALUES (?,?,?,?,?)",
                 ("https://example.com/4", "", "error", "404", old))
    conn.commit()
    assert job_descriptions.cached(conn, "https://example.com/4") is None
    conn.close()


def test_a_posting_with_no_url_is_marked_unfetched(tmp_path):
    conn = job_descriptions.connect(tmp_path / "c.db")
    jobs = [{"url": "", "text": ""}]
    job_descriptions.fetch_missing(jobs, conn=conn, verbose=False)
    assert jobs[0]["description_fetched"] is False
    conn.close()
