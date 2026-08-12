"""
JD corpus domain restriction and schema (issue #177).

Two concerns, both cheap and both previously unguarded:

* the **classification layer** in `scripts/scrape_job_descriptions.py`, which
  decides what counts as an intern/entry posting in one of five role families.
  Its predecessor filtered `intern` *out* — the exact population the product
  targets — so the level filter gets a regression test pointing the right way.
* the **corpus schema**: every task file in `eval/jd_dataset/` must carry
  `role_family` and `level` from their closed sets, because `_aggregate` cannot
  slice on a label the dataset does not carry.

No network access: the scraper's pure functions are tested directly.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from scrape_job_descriptions import (  # noqa: E402
    LEVELS,
    MAX_ENTRY_YEARS,
    MIN_DESCRIPTION_CHARS,
    ROLE_FAMILY_NAMES,
    allocate,
    build_posting,
    classify_family,
    classify_level,
    min_stated_years,
)

DATASET_DIR = ROOT / "eval" / "jd_dataset"

# Long enough to clear MIN_DESCRIPTION_CHARS without carrying a years figure.
_FILLER = "We build tools people rely on. " * 40


# ── role family ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    ("Machine Learning Engineer", "ml_engineering"),
    ("ML Engineer, Ranking", "ml_engineering"),
    ("AI Engineer", "ai_engineering"),
    ("Data Scientist, Growth", "data_science"),
    ("Data Engineer", "data_engineering"),
    ("Analytics Engineer", "data_engineering"),
    ("Software Engineer I", "software_engineering"),
    ("Backend Engineer", "software_engineering"),
    ("Full Stack Engineer", "software_engineering"),
])
def test_classify_family_covers_the_five_target_families(title, expected):
    assert classify_family(title) == expected


def test_family_order_is_load_bearing():
    """A hybrid title must land in the specific family, not the generic one.

    `software_engineering`'s pattern matches "Software Engineer" inside
    "Machine Learning Software Engineer", so this only passes because
    ml_engineering is declared first.
    """
    assert classify_family("Machine Learning Software Engineer") == "ml_engineering"
    assert classify_family("Software Engineer, Generative AI") == "ai_engineering"
    # ...but a plain platform role stays software engineering: "AI Platform" is
    # the team, not the discipline, and over-claiming it would inflate the
    # ai_engineering stratum with ordinary backend work.
    assert classify_family("Software Engineer, AI Platform") == "software_engineering"


@pytest.mark.parametrize("title", [
    "Product Designer", "Account Executive", "Technical Recruiter",
    "Mechanical Engineer", "",
])
def test_off_domain_titles_are_rejected(title):
    assert classify_family(title) is None


# ── level ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title", [
    "Software Engineer Intern",
    "Data Science Internship - Summer 2027",
    "ML Engineer Co-op",
])
def test_intern_titles_are_kept_not_filtered(title):
    """The regression this issue exists for.

    The previous EXCLUDE regex listed `intern`, so the scraper filtered out the
    product's target population (issue #177).
    """
    assert classify_level(title, _FILLER) == "intern"


@pytest.mark.parametrize("title", [
    "New Grad Software Engineer",
    "Software Engineer, University Graduate",
    "Entry-Level Data Analyst",
    "Software Engineer I",
    "Associate Data Scientist",
    "Junior Full Stack Developer",
    "Jr. Data Scientist",
])
def test_entry_titles_are_recognized(title):
    assert classify_level(title, _FILLER) == "entry"


@pytest.mark.parametrize("title", [
    "Senior Machine Learning Engineer",
    "Sr. Data Engineer",
    "Staff Software Engineer",
    "Principal AI Engineer",
    "Engineering Manager, Data",
    "Director of Data Science",
    "Software Engineer II",
    "Software Engineer III",
])
def test_seniority_markers_are_disqualifying(title):
    assert classify_level(title, _FILLER) is None


def test_an_intern_posting_beats_a_seniority_marker_in_the_same_title():
    """"Senior" here modifies the student's year, not the rung."""
    assert classify_level("Senior-Year Software Engineering Intern", _FILLER) == "intern"


# ── years-of-experience gate ───────────────────────────────────────────────────

def test_unlevelled_title_falls_back_to_stated_years():
    entry = "Requirements:\n- 0-2 years of industry experience\n" + _FILLER
    senior = "Requirements:\n- 6+ years of industry experience\n" + _FILLER
    assert classify_level("Machine Learning Engineer", entry) == "entry"
    assert classify_level("Machine Learning Engineer", senior) is None


def test_years_gate_takes_the_minimum_not_the_maximum():
    """A nice-to-have line must not disqualify an otherwise entry posting."""
    text = ("- 1+ years of experience required\n"
            "- 5 years of experience with distributed systems preferred\n") + _FILLER
    assert min_stated_years(text) == 1
    assert classify_level("Data Engineer", text) == "entry"


def test_years_gate_is_absent_when_the_posting_states_none():
    assert min_stated_years(_FILLER) is None
    assert classify_level("Data Engineer", _FILLER) is None


def test_max_entry_years_boundary_is_inclusive():
    at_cap = f"- {MAX_ENTRY_YEARS} years of experience\n" + _FILLER
    over = f"- {MAX_ENTRY_YEARS + 1} years of experience\n" + _FILLER
    assert classify_level("Data Scientist", at_cap) == "entry"
    assert classify_level("Data Scientist", over) is None


# ── posting assembly ───────────────────────────────────────────────────────────

def _posting(title="Software Engineer I", description=None, company="Acme"):
    """A candidate posting.

    The default description is made unique per (company, title): `dedupe` now
    collapses postings that share a body, so a shared filler would silently
    reduce every multi-posting fixture to one row and make the allocation tests
    assert nothing.
    """
    if description is None:
        description = f"Role at {company}: {title}.\n" + _FILLER
    return build_posting(
        source="greenhouse", company=company, title=title,
        location="Remote", url="https://example.com/j/1",
        description=description,
    )


def test_build_posting_labels_family_and_level():
    p = _posting()
    assert p["role_family"] == "software_engineering"
    assert p["level"] == "entry"
    assert p["id"] == "acme_software_engineer_i"


def test_build_posting_rejects_short_descriptions():
    assert _posting(description="too short") is None


def test_build_posting_rejects_out_of_domain_and_senior():
    assert _posting(title="Product Designer") is None
    assert _posting(title="Senior Software Engineer") is None


def test_min_description_chars_is_actually_enforced():
    just_under = "x" * (MIN_DESCRIPTION_CHARS - 1)
    assert _posting(description=just_under) is None


# ── allocation ─────────────────────────────────────────────────────────────────

def test_allocate_caps_each_family_independently():
    candidates = [
        _posting(title="Software Engineer I", company=f"Co{i:02d}") for i in range(5)
    ] + [
        _posting(title="Data Engineer", company=f"Co{i:02d}",
                 description=f"Data role {i}.\n- 1 years of experience\n" + _FILLER)
        for i in range(5)
    ]
    kept = allocate(candidates, per_family=2)
    by_family = {}
    for p in kept:
        by_family.setdefault(p["role_family"], []).append(p)
    assert len(by_family["software_engineering"]) == 2
    assert len(by_family["data_engineering"]) == 2


def test_allocate_is_deterministic_and_content_ordered():
    """Selection must not depend on board response order (#158/#171)."""
    candidates = [
        _posting(title="Software Engineer I", company=f"Co{i:02d}") for i in range(6)
    ]
    forward = allocate(candidates, per_family=3)
    backward = allocate(list(reversed(candidates)), per_family=3)
    assert [p["id"] for p in forward] == [p["id"] for p in backward]
    assert [p["company"] for p in forward] == ["Co00", "Co01", "Co02"]


def test_allocate_spreads_across_employers_rather_than_taking_a_prefix():
    """One prolific employer must not fill the whole family.

    Taking the first N of a company-sorted list would hand a 30-slot family
    entirely to companies beginning with "A" — deterministic, and useless as a
    sample of the market.
    """
    candidates = (
        [_posting(title=f"New Grad Software Engineer {i}", company="Aardvark Corp")
         for i in range(20)]
        + [_posting(title="Software Engineer I", company="Zebra Inc")]
        + [_posting(title="Software Engineer I", company="Mango Ltd")]
    )
    kept = allocate(candidates, per_family=3)
    assert sorted(p["company"] for p in kept) == ["Aardvark Corp", "Mango Ltd", "Zebra Inc"]


def test_a_prolific_employer_still_fills_remaining_slots():
    """Round-robin spreads, it does not cap: with nobody else left to take a
    turn, the deep queue keeps contributing."""
    candidates = (
        [_posting(title=f"New Grad Software Engineer {i}", company="Aardvark Corp")
         for i in range(5)]
        + [_posting(title="Software Engineer I", company="Zebra Inc")]
    )
    kept = allocate(candidates, per_family=4)
    counts = {}
    for p in kept:
        counts[p["company"]] = counts.get(p["company"], 0) + 1
    assert counts == {"Aardvark Corp": 3, "Zebra Inc": 1}


def test_allocate_dedupes_the_same_role_posted_in_many_locations():
    dupes = [_posting() for _ in range(4)]
    assert len(allocate(dupes, per_family=10)) == 1


def test_dedupe_collapses_company_spelling_variants():
    """`AEG` / `AEG Worldwide` and `ASSYST, Inc.` / `Assyst` are one employer.

    The id is built from the company string, so a spelling variant slips past an
    id check. All three of these fired on the first real pull.
    """
    pairs = [("AEG", "AEG Worldwide"), ("Aquatic Capital", "Aquatic"),
             ("ASSYST, Inc.", "Assyst")]
    for a, b in pairs:
        kept = allocate([_posting(company=a), _posting(company=b)], per_family=10)
        assert len(kept) == 1, f"{a!r} / {b!r} were not collapsed"


def test_dedupe_collapses_identical_descriptions_across_ids():
    """GDIT and General Dynamics Information Technology posted one internship
    twice under different company strings and the same body."""
    kept = allocate([
        _posting(company="GDIT", description="Shared body. " + _FILLER),
        _posting(company="General Dynamics Information Technology",
                 description="Shared body. " + _FILLER),
    ], per_family=10)
    assert len(kept) == 1


def test_a_shared_description_never_lands_in_two_role_families():
    """The defect that matters most.

    Microsoft posted one body under both an AI/ML title and a Data Platform
    title, putting the same document into two families. A per-family contrast
    built on a shared document is partly comparing a posting with itself, and no
    sample size fixes that.
    """
    body = "One shared posting body. " + _FILLER
    kept = allocate([
        _posting(company="Microsoft", title="Software Engineer Intern, AI/ML",
                 description=body),
        _posting(company="Microsoft", title="Software Engineer Intern, Data Platform",
                 description=body),
    ], per_family=10)
    assert len(kept) == 1
    assert len({p["role_family"] for p in kept}) == 1


def test_similar_company_names_are_not_collapsed_when_the_role_differs():
    """Prefix matching is guarded by an identical title.

    "Meta" and "Metabase" share a prefix; only an identical role makes that
    evidence of one employer rather than two.
    """
    kept = allocate([
        _posting(company="Meta", title="Software Engineer I"),
        _posting(company="Metabase", title="Data Engineer I"),
    ], per_family=10)
    assert len(kept) == 2


def test_a_partial_word_is_not_an_employer_prefix():
    """"AB" is not a word-prefix of "ABC Systems" — `abc` is a different word.

    A raw string prefix would collapse these; word tokens do not, and that holds
    with no length threshold to tune.
    """
    kept = allocate([
        _posting(company="AB", title="Software Engineer I"),
        _posting(company="ABC Systems", title="Software Engineer I"),
    ], per_family=10)
    assert len(kept) == 2


def test_corporate_suffixes_do_not_block_a_match():
    """"ASSYST, Inc." and "Assyst" are one employer."""
    kept = allocate([
        _posting(company="ASSYST, Inc.", title="Junior Full Stack Developer"),
        _posting(company="Assyst", title="Junior Full Stack Developer"),
    ], per_family=10)
    assert len(kept) == 1


def test_dedupe_keeps_genuinely_distinct_postings():
    kept = allocate([
        _posting(company="Acme", title="Software Engineer I",
                 description="Body one. " + _FILLER),
        _posting(company="Acme", title="Data Engineer I",
                 description="Body two. " + _FILLER),
    ], per_family=10)
    assert len(kept) == 2


def test_no_two_tasks_in_the_corpus_share_a_description():
    """Asserted on the shipped corpus, not just the filter."""
    import hashlib
    seen = {}
    for path in _corpus():
        task = json.loads(path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(task["description"].encode("utf-8")).hexdigest()
        assert digest not in seen, f"{path.name} duplicates {seen.get(digest)}"
        seen[digest] = path.name


# ── corpus schema ──────────────────────────────────────────────────────────────

def _corpus():
    return sorted(DATASET_DIR.glob("*.json"))


def test_corpus_is_present():
    assert _corpus(), f"no task files in {DATASET_DIR}"


def test_every_task_carries_a_valid_role_family_and_level():
    offenders = []
    for path in _corpus():
        task = json.loads(path.read_text(encoding="utf-8"))
        if task.get("role_family") not in ROLE_FAMILY_NAMES:
            offenders.append(f"{path.name}: role_family={task.get('role_family')!r}")
        if task.get("level") not in LEVELS:
            offenders.append(f"{path.name}: level={task.get('level')!r}")
    assert not offenders, "\n".join(offenders)


def test_no_posting_in_the_corpus_is_senior():
    """The #177 defect, asserted on the shipped corpus rather than the filter."""
    from scrape_job_descriptions import SENIOR_TITLE
    senior = [
        json.loads(p.read_text(encoding="utf-8"))["title"]
        for p in _corpus()
        if SENIOR_TITLE.search(json.loads(p.read_text(encoding="utf-8"))["title"])
    ]
    assert not senior, f"senior postings in the corpus: {senior}"


def test_every_task_has_the_fields_the_benchmark_reads():
    required = ("id", "source", "company", "title", "url", "description", "scraped_at")
    for path in _corpus():
        task = json.loads(path.read_text(encoding="utf-8"))
        missing = [k for k in required if not task.get(k)]
        assert not missing, f"{path.name} missing {missing}"
        assert len(task["description"]) >= MIN_DESCRIPTION_CHARS, path.name


# ── stratified sampling ────────────────────────────────────────────────────────

def test_limit_samples_across_families_not_off_the_front():
    """A stratified corpus must not be sampled in a way that destroys the strata.

    Files sort alphabetically by company, so `tasks[:limit]` returns whichever
    families happen to start with "A" — and silently, since every reported
    number still looks well-formed.
    """
    from eval.tailoring_benchmark import load_tasks

    tasks = load_tasks(limit=10)
    assert len(tasks) == 10
    families = {t["role_family"] for t in tasks}
    assert len(families) == len(ROLE_FAMILY_NAMES), families
    counts = {f: sum(1 for t in tasks if t["role_family"] == f) for f in families}
    assert set(counts.values()) == {2}, counts


def test_limit_sampling_is_reproducible_and_in_corpus_order():
    """Order is load-bearing: JobCard injection (#137) feeds earlier tasks into
    later prompts, so a run's sequence must not depend on how it was drawn."""
    from eval.tailoring_benchmark import load_tasks

    first, second = load_tasks(limit=10), load_tasks(limit=10)
    assert [t["id"] for t in first] == [t["id"] for t in second]
    assert [t["id"] for t in first] == sorted(t["id"] for t in first)


def test_limit_at_or_above_corpus_size_returns_everything():
    from eval.tailoring_benchmark import load_tasks

    everything = load_tasks()
    assert len(load_tasks(limit=len(everything) + 50)) == len(everything)
    assert len(load_tasks(limit=0)) == len(everything)
