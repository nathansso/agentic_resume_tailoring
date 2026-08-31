"""Profile-fixture parsing for the benchmark harness (issue #171, chunk 1.2).

`eval/tailoring_benchmark.py` carried the "canned parse" of the one benchmark
profile as three hand-maintained module constants. That duplicate blocked a
second profile — the foundation #172's dataset work needs — and had already
drifted from the fixture it claimed to mirror. These tests cover the parser that
replaced it, and pin the property that actually matters: an arbitrary second
profile parses with no Python change.
"""
import textwrap

import pytest

from eval.profile_fixture import (
    DEFAULT_CATEGORY,
    load_profile,
    parse_profile_text,
)
from eval.tailoring_benchmark import PROFILES_DIR

# Bound to the *legacy* fixture rather than to the benchmark's DEFAULT_PROFILE
# (#182 re-pointed that at a live profile). This is the right target, not a
# workaround: these tests pin the parser against a hand-written, frozen fixture,
# whereas the 20 live profiles are generated from `eval/profile_banks.py` and
# change whenever the bank does. The property that matters — "an arbitrary
# second profile parses with no Python change" — is covered below by
# SECOND_PROFILE, not by whichever profile the harness happens to default to.
LEGACY_PROFILE = PROFILES_DIR / "benchmark_profile.md"


# ── the shipped fixture ────────────────────────────────────────────────────────

def test_benchmark_profile_parses_into_all_three_families():
    profile = load_profile(LEGACY_PROFILE)
    assert len(profile.experiences) == 4
    assert len(profile.projects) == 4
    assert len(profile.skills) == 32


def test_experience_dates_and_bullets_survive_the_parse():
    profile = load_profile(LEGACY_PROFILE)
    first = profile.experiences[0]
    assert first["company"] == "Nimbus Analytics"
    assert first["title"] == "Machine Learning Engineer"
    assert (first["start_date"], first["end_date"]) == ("2024-01", "Present")
    assert len(first["bullets"]) == 4
    assert first["bullets"][0].startswith("Built and deployed gradient-boosted")

    closed = profile.experiences[1]
    assert (closed["start_date"], closed["end_date"]) == ("2022-03", "2024-01")


def test_project_repo_url_and_descriptor_are_parsed():
    profile = load_profile(LEGACY_PROFILE)
    by_name = {p["name"]: p for p in profile.projects}
    assert by_name["SemanticSearch-Lite"]["repo_url"] == \
        "https://github.com/alexrivera/semsearch"
    assert by_name["SemanticSearch-Lite"]["description"] == "Semantic search library"
    # A project with no parenthetical carries no repo_url rather than an empty one.
    assert "repo_url" not in by_name["StreamBoard"]


def test_every_source_bullet_is_reachable():
    profile = load_profile(LEGACY_PROFILE)
    assert len(profile.bullets) == sum(
        len(i["bullets"]) for i in (*profile.experiences, *profile.projects)
    )
    assert all(b.strip() for b in profile.bullets)


def test_parse_covers_the_skills_the_constants_had_drifted_from():
    """The old hand-copied list held 30 of the fixture's 32 skills.

    PHP and the OpenAI API were in the markdown the benchmark actually ingests
    but not in the constants the stub "extracted" from it — the concrete drift
    that motivated deriving the parse instead of maintaining it.
    """
    names = {s["name"] for s in load_profile(LEGACY_PROFILE).skills}
    assert {"PHP", "OpenAI API"} <= names


# ── derived skill metadata ─────────────────────────────────────────────────────

def test_skill_categories_come_from_the_shared_dictionary():
    by_name = {s["name"]: s for s in load_profile(LEGACY_PROFILE).skills}
    assert by_name["Python"]["category"] == "Language"
    assert by_name["PyTorch"]["category"] == "Library"
    assert by_name["Postgres"]["category"] == "Database"
    assert by_name["AWS"]["category"] == "Cloud"
    assert by_name["Docker"]["category"] == "Tool"


def test_proficiency_tracks_evidence_density_rather_than_being_constant():
    """A constant would silently retire a scoring dimension (weight 0.10)."""
    by_name = {s["name"]: s for s in load_profile(LEGACY_PROFILE).skills}
    # Mentioned in two or more bullets / one / none.
    assert by_name["Python"]["proficiency"] == 5
    assert by_name["PyTorch"]["proficiency"] == 4
    assert by_name["Kubernetes"]["proficiency"] == 3
    assert len({s["proficiency"] for s in by_name.values()}) > 1


def test_skill_term_matching_respects_word_boundaries():
    """`SQL` must not count itself inside `MySQL` or `SQLAlchemy`."""
    text = textwrap.dedent("""
        # T

        ## Experience

        **Co** — Eng (Jan 2020 – Present)
        - Used MySQL and SQLAlchemy daily.

        ## Projects

        **P** — a project
        - Something unrelated.

        ## Skills
        SQL
    """)
    skills = {s["name"]: s for s in parse_profile_text(text).skills}
    assert skills["SQL"]["proficiency"] == 3  # zero real mentions


# ── a second profile works with no Python change (the #172 prerequisite) ───────

SECOND_PROFILE = textwrap.dedent("""
    # Jordan Vale

    jordan@example.com

    ## Summary
    Data engineer.

    ## Experience

    **Tidewater Data** — Senior Data Engineer (Feb 2023 – Present)
    - Built Spark pipelines processing 3TB daily into a Delta lakehouse.
    - Cut warehouse spend 30% by rewriting Airflow DAGs around incremental loads.

    **Kestrel Systems** — Data Engineer (2019 – Feb 2023)
    - Maintained Terraform modules for the analytics platform.

    ## Projects

    **dbt-lineage** (github.com/jordanvale/dbt-lineage) — Column-level lineage CLI
    - Column-level lineage for dbt projects; 120 GitHub stars.

    **Chartsmith**
    - Static chart generator in Go.

    ## Skills
    Python, Go, Spark, Airflow, Terraform, Snowflake, Obscure-Inhouse-Tool

    ## Education
    B.S. Statistics, Coastal University (2019)
""")


def test_a_second_profile_parses_with_no_code_change():
    profile = parse_profile_text(SECOND_PROFILE)
    assert [e["company"] for e in profile.experiences] == \
        ["Tidewater Data", "Kestrel Systems"]
    assert profile.experiences[0]["start_date"] == "2023-02"
    # A bare year is kept as a year rather than guessed into a month.
    assert profile.experiences[1]["start_date"] == "2019"
    assert [p["name"] for p in profile.projects] == ["dbt-lineage", "Chartsmith"]
    assert profile.projects[0]["repo_url"] == "https://github.com/jordanvale/dbt-lineage"
    assert len(profile.skills) == 7


def test_unknown_skill_names_fall_back_rather_than_needing_a_dictionary_entry():
    """The category table is a shared dictionary, not a per-profile fixture."""
    by_name = {s["name"]: s for s in parse_profile_text(SECOND_PROFILE).skills}
    assert by_name["Obscure-Inhouse-Tool"]["category"] == DEFAULT_CATEGORY
    assert by_name["Spark"]["proficiency"] == 4  # one bullet mentions it


def test_a_malformed_profile_fails_loudly():
    with pytest.raises(ValueError, match="parsed empty"):
        parse_profile_text("# Someone\n\nNo headings at all.\n")


# ── education & achievements (issue #172, #171's recorded follow-on) ──────────

_MINIMAL = """# Sam Chen

## Experience

**Acme** — Data Engineer (Jan 2024 – Present)
- Built pipelines.

## Projects

**Thing** — A thing
- Did the thing.

## Skills
Python, SQL
"""


def _parsed(extra: str):
    from eval.profile_fixture import parse_profile_text
    return parse_profile_text(_MINIMAL + extra)


def test_education_is_parsed_from_the_markdown():
    """Plumbing mode rendered no education section at all before this.

    `_stub_payload` returned `{}` for the education prompt, so a section the
    product ships was invisible to every plumbing number (#171's follow-on).
    """
    row = _parsed("\n## Education\nB.S. Computer Science, City University (2021)\n"
                  ).education[0]
    assert row["degree"] == "B.S. Computer Science"
    assert row["institution"] == "City University"
    assert row["end_date"] == "2021"


def test_a_lone_year_is_a_graduation_date_not_a_start_date():
    row = _parsed("\n## Education\nB.S. Math, State (2021)\n").education[0]
    assert row["start_date"] is None and row["end_date"] == "2021"


def test_education_date_ranges_split():
    row = _parsed("\n## Education\nB.S. Math, State (Sep 2021 – Jun 2025)\n").education[0]
    assert row["start_date"] == "2021-09" and row["end_date"] == "2025-06"


def test_education_gpa_is_extracted_and_removed_from_the_degree():
    row = _parsed("\n## Education\nB.S. Math, State (2025), GPA: 3.92\n").education[0]
    assert row["gpa"] == "3.92"
    assert "GPA" not in (row["degree"] or "") and "GPA" not in (row["institution"] or "")


def test_an_unparseable_education_line_is_kept_not_dropped():
    """A fixture author should see their line render oddly, not vanish."""
    rows = _parsed("\n## Education\nSelf-taught\n").education
    assert len(rows) == 1 and rows[0]["degree"] == "Self-taught"


def test_achievements_parse_title_date_and_description():
    row = _parsed("\n## Achievements\n- **1st Place, HackMIT** (2024) — Built a "
                  "real-time translator\n").achievements[0]
    assert row["title"] == "1st Place, HackMIT"
    assert row["date"] == "2024"
    assert row["description"] == "Built a real-time translator"


def test_achievements_accept_alternate_headings():
    for heading in ("Achievements", "Honors", "Awards"):
        rows = _parsed(f"\n## {heading}\n- Dean's List\n").achievements
        assert [r["title"] for r in rows] == ["Dean's List"], heading


def test_a_profile_with_neither_section_still_parses():
    fixture = _parsed("")
    assert fixture.education == [] and fixture.achievements == []


def test_the_stub_routes_education_and_achievements_to_the_profile():
    """The canned payload must answer the real prompts, not just parse.

    `bind_fixture` writes a module global in `eval.tailoring_benchmark`, so the
    binding is restored on the way out. Without that, every test running later
    in the same process saw this fixture instead of the harness default — which
    was invisible while the two were the same file, and became a cross-file
    failure the moment #182 re-pointed DEFAULT_PROFILE.
    """
    from agents.parser import ResumeParserAgent  # noqa: F401  (import guard)
    from eval import tailoring_benchmark as tb

    previous = tb._FIXTURE
    try:
        tb.bind_fixture(LEGACY_PROFILE)
        education_prompt = ("You are an expert resume parser. Extract education "
                            "entries from the text.")
        achievement_prompt = ("You are an expert resume parser. Extract achievements, "
                              "honors, and awards from the text.")
        assert tb._stub_payload(education_prompt), "education still compiles to nothing"
        assert isinstance(tb._stub_payload(achievement_prompt), list)
    finally:
        tb._FIXTURE = previous
