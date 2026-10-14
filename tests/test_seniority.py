"""Seniority-tier detection (issue #181).

`_detect_level` used to match bare substrings and return the highest tier found
anywhere, so one incidental word promoted a whole document: 124 of 150 intern /
entry postings and the benchmark profile read at the wrong tier. These tests pin
the words that did it, the title-over-body ordering, and the explicit `unknown`
tier. Model-free and database-free.
"""
import json
from pathlib import Path

import pytest

from agents.ats_scorer import _detect_level
from agents.seniority import (
    LEVEL_ORDER,
    UNKNOWN,
    detect_level,
    is_above_entry_title,
    level_index,
    min_stated_years,
    title_level,
)

ROOT = Path(__file__).resolve().parent.parent

_PADDING = "We build tools people rely on every day.\n"


def _posting(*lines: str) -> str:
    return "\n".join(lines) + "\n" + _PADDING * 3


# ── the words that used to promote a document ─────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "You will be leading a small project",          # lead
    "Our leadership values curiosity",              # lead
    "Check the leaderboard weekly",                 # lead
    "Staffing agencies need not apply",             # staff
    "Join our staff members at the offsite",        # staff
    "Experience with principal component analysis", # principal
    "You will report to the hiring manager",        # manager
    "Your manager will onboard you",                # manager
    "Exposure to association rules mining",         # associate
    "Anything associated with the data platform",   # associate
    "Presented to the senior leadership team",      # senior
    "Completed a senior capstone project",          # senior
])
def test_incidental_words_in_a_posting_body_do_not_promote_it(phrase):
    assert detect_level(_posting("About the job", phrase)) == UNKNOWN


@pytest.mark.parametrize("phrase", [
    "Automated report generation with Python scripts, saving staff ten hours weekly",
    "Mentored junior engineers on code review",
    "Collaborated with senior stakeholders on roadmap planning",
    "Applied principal component analysis to 2M-row feature tables",
    "Reported to the engineering manager each sprint",
    "Led the migration from a monolith to event-driven services",
    "Gave a talk on association rules to the leadership team",
])
def test_incidental_words_in_a_candidate_do_not_promote_it(phrase):
    assert detect_level(f"Alex Rivera\n\nExperience\n- {phrase}\n{phrase}",
                        kind="candidate") == UNKNOWN


def test_the_benchmark_profile_no_longer_reads_as_lead():
    """The #181 headline: this fixture read `lead` because of 'saving staff'."""
    text = (ROOT / "eval" / "profiles" / "benchmark_profile.md").read_text(encoding="utf-8")
    assert "saving staff ten hours" in text
    assert detect_level(text, kind="candidate") != "lead"
    assert detect_level(text, kind="candidate") in ("intern", "junior", UNKNOWN)


# ── title over body ───────────────────────────────────────────────────────────

def test_the_title_outranks_every_body_word():
    jd = _posting(
        "Data Science Intern",
        "You will work with the hiring manager, senior engineers and the staff.",
        "Our principal engineers and team lead run weekly reviews.",
    )
    assert detect_level(jd) == "intern"


def test_an_explicit_title_beats_the_first_line_and_the_body():
    jd = _posting("Company overview", "We are hiring a senior leader.")
    assert detect_level(jd, title="Junior Data Engineer") == "junior"
    assert detect_level(jd, title="Staff Machine Learning Engineer") == "lead"


@pytest.mark.parametrize("title,expected", [
    ("Software Engineer Intern", "intern"),
    ("Junior Data Scientist", "junior"),
    ("Associate Software Engineer", "junior"),
    ("Software Engineer I", "junior"),
    ("Software Engineer II", "mid"),
    ("Senior Machine Learning Engineer", "senior"),
    ("Sr. Data Engineer", "senior"),
    ("Staff Software Engineer", "lead"),
    ("Principal AI Engineer", "lead"),
    ("Tech Lead, Data Platform", "lead"),
    ("Engineering Manager, Data", "manager"),
    ("Director of Data Science", "manager"),
    ("Machine Learning Engineer", None),
])
def test_title_level(title, expected):
    assert title_level(title, "title") == expected


def test_an_intern_marker_beats_a_seniority_word_in_the_same_title():
    assert title_level("Senior-Year Software Engineering Intern", "title") == "intern"
    assert title_level("Senior Data Intern", "title") == "intern"


def test_a_senior_family_marker_beats_associate():
    assert title_level("Associate Director, Data Science", "title") == "manager"


def test_bare_lead_staff_principal_need_a_role_noun_unless_the_title_is_known():
    # Inferred heading or résumé line: "Staff Benefits" is not a rung.
    assert title_level("Staff Benefits", "posting") is None
    assert title_level("Principal component analysis", "posting") is None
    assert title_level("Lead Generation Analyst", "posting") is None
    # A known job title: a bare marker is a rung.
    assert title_level("Staff, Data", "title") == "lead"


def test_is_above_entry_title_is_what_the_scraper_disqualifies_on():
    assert is_above_entry_title("Software Engineer II")
    assert is_above_entry_title("Lead Data Scientist")
    assert not is_above_entry_title("Software Engineer I")
    assert not is_above_entry_title("Data Science Intern")
    assert not is_above_entry_title("Machine Learning Engineer")


# ── the body, when the title says nothing ─────────────────────────────────────

def test_self_referential_body_phrases_set_the_tier():
    assert detect_level(_posting("About us", "This is an entry-level role.")) == "junior"
    assert detect_level(_posting("About us", "We welcome recent graduates.")) == "junior"
    assert detect_level(_posting("About us", "This internship runs 12 weeks.")) == "intern"
    assert detect_level(_posting("About us", "You will have 3 direct reports.")) == "manager"


def test_a_hiring_sentence_names_the_role_but_a_pairing_sentence_does_not():
    assert detect_level(_posting("About us", "We are hiring a senior engineer.")) == "senior"
    assert detect_level(_posting("About us", "We are looking for a motivated junior.")) == "junior"
    assert detect_level(_posting("About us", "You will pair with a senior engineer.")) == UNKNOWN
    assert detect_level(_posting("About us", "You will mentor junior engineers.")) == UNKNOWN


@pytest.mark.parametrize("years,expected", [
    (0, "junior"), (2, "junior"), (3, "mid"), (4, "mid"), (5, "senior"), (10, "senior"),
])
def test_stated_years_map_to_a_tier(years, expected):
    jd = _posting("About us", f"- {years}+ years of professional experience required")
    assert detect_level(jd) == expected


def test_years_use_the_minimum_stated():
    jd = _posting("About us", "- 1+ years of experience\n- 5 years of experience preferred")
    assert min_stated_years(jd) == 1
    assert detect_level(jd) == "junior"


def test_years_that_do_not_describe_experience_are_ignored():
    jd = _posting("About us", "Founded 40 years ago, with 13,000+ people worldwide.")
    assert min_stated_years(jd) is None
    assert detect_level(jd) == UNKNOWN


def test_a_posting_that_states_nothing_is_unknown_not_mid():
    assert detect_level(_posting("We build things.")) == UNKNOWN
    assert detect_level("") == UNKNOWN
    assert detect_level(None) == UNKNOWN


# ── candidates ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line,expected", [
    ("Data Science Intern at Acme", "intern"),
    ("**Nimbus Analytics** — Senior Machine Learning Engineer (2022 – Present)", "senior"),
    ("Junior Developer, Harbor Labs", "junior"),
    ("Software Engineer II at Bluefin", "mid"),
    ("Staff Software Engineer at Bluefin", "lead"),
])
def test_candidate_role_lines_set_the_tier(line, expected):
    assert detect_level(line, kind="candidate") == expected


def test_a_candidate_takes_the_highest_role_tier():
    text = "Data Science Intern at Acme\nSenior Data Scientist at Bluefin\n"
    assert detect_level(text, kind="candidate") == "senior"


@pytest.mark.parametrize("line", [
    "Research Associate, Vision Lab",
    "Sales Associate at a campus store",
    "Teaching Assistant, University of California",
    "Data Science Fellow",
    "Hackathon Project Lead",
])
def test_candidate_roles_that_are_not_a_rung_stay_unknown(line):
    assert detect_level(line, kind="candidate") == UNKNOWN


def test_a_candidate_summary_years_are_used_when_no_role_names_a_tier():
    assert detect_level("Engineer with 6+ years of experience in Python",
                        kind="candidate") == "senior"


def test_a_long_prose_line_is_not_a_role_line():
    prose = ("Senior leadership praised the migration, which I led as the intern "
             "mentor for the whole summer cohort across three offices worldwide.")
    assert detect_level(prose, kind="candidate") == UNKNOWN


# ── ordering, compatibility ───────────────────────────────────────────────────

def test_level_order_is_ascending_and_excludes_unknown():
    assert LEVEL_ORDER == ("intern", "junior", "mid", "senior", "lead", "manager")
    assert UNKNOWN not in LEVEL_ORDER


def test_level_index_tolerates_stored_and_unrecognised_strings():
    """Stored JD profiles hold whatever the old detector wrote, `mid` included."""
    assert level_index("mid") == 2
    assert level_index("manager") == 5
    assert level_index(UNKNOWN) is None
    assert level_index("principal") is None
    assert level_index(None) is None


def test_the_scorers_detector_is_the_shared_one():
    jd = _posting("Senior Data Engineer", "Build pipelines.")
    assert _detect_level(jd) == detect_level(jd) == "senior"
    assert _detect_level("Data Science Intern at Acme", kind="candidate") == "intern"


# ── the committed corpus ──────────────────────────────────────────────────────

def test_the_committed_corpus_is_read_at_its_filed_tier():
    """The measurement #181 was filed on: 124/150 postings were mislabelled.

    A posting may read at its filed tier or state none (`unknown`); reading at a
    *different* tier is a mislabel. 6 remain, all postings whose own text states
    a different level than their title (a 5+ years body under an entry title,
    `Engineer IV`); the ceiling is set just above that so a regression to
    substring matching, which fails ~100, cannot hide.
    """
    allowed = {"intern": {"intern"}, "entry": {"intern", "junior"}}
    wrong = []
    for path in sorted((ROOT / "eval" / "jd_dataset").glob("*.json")):
        task = json.loads(path.read_text(encoding="utf-8"))
        tier = detect_level(task["description"])
        if tier != UNKNOWN and tier not in allowed[task["level"]]:
            wrong.append(f"{path.stem}: filed {task['level']}, read {tier}")
    assert len(wrong) <= 10, "\n".join(wrong)
