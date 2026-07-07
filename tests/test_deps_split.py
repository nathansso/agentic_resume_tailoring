"""Tests for the deps-split feature (issue #29).

Covers:
  1. Structural correctness of requirements-core.txt and requirements-full.txt
  2. Graceful degradation when sentence-transformers is absent (matcher falls back to exact)
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REQ_CORE = ROOT / "requirements-core.txt"
REQ_FULL = ROOT / "requirements-full.txt"
REQ_ALL  = ROOT / "requirements.txt"

# Packages deliberately excluded from core (full-only extras; require heavy
# post-install steps — e.g. `playwright install chromium` — or pull in
# PyTorch, which OOM-kills the 512 MB web VM).
HEAVYWEIGHT = {"sentence_transformers", "playwright", "docling"}


def _direct_packages(path: Path) -> set[str]:
    """Return normalized package names from a requirements file.

    Skips comment lines and -r include directives.
    Normalises names to lowercase with hyphens replaced by underscores.
    """
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-r"):
            continue
        pkg = re.split(r"[=<>!\[;]", line)[0].strip().lower().replace("-", "_")
        if pkg:
            names.add(pkg)
    return names


# ---------------------------------------------------------------------------
# Structural tests — file parsing only, no network
# ---------------------------------------------------------------------------

def test_requirements_core_exists():
    assert REQ_CORE.exists(), "requirements-core.txt is missing from the repo root"


def test_requirements_full_exists():
    assert REQ_FULL.exists(), "requirements-full.txt is missing from the repo root"


def test_core_excludes_sentence_transformers():
    assert "sentence_transformers" not in _direct_packages(REQ_CORE), \
        "sentence-transformers must NOT appear in requirements-core.txt"


def test_full_includes_sentence_transformers():
    content = REQ_FULL.read_text(encoding="utf-8").lower()
    assert "sentence-transformers" in content or "sentence_transformers" in content, \
        "sentence-transformers must appear in requirements-full.txt"


def test_core_excludes_docling():
    assert "docling" not in _direct_packages(REQ_CORE), \
        "docling must NOT appear in requirements-core.txt (pulls in PyTorch; OOM-kills the web VM)"


def test_core_includes_pypdf_fallback():
    assert "pypdf" in _direct_packages(REQ_CORE), \
        "pypdf must appear in requirements-core.txt — it is the docling-free PDF extraction fallback"


def test_full_includes_docling():
    assert "docling" in _direct_packages(REQ_FULL), \
        "docling must appear in requirements-full.txt"


def test_full_references_core():
    """requirements-full.txt must extend core via -r rather than duplicating it."""
    content = REQ_FULL.read_text(encoding="utf-8")
    assert "-r requirements-core.txt" in content, \
        "requirements-full.txt must include '-r requirements-core.txt'"


def test_core_covers_all_requirements_txt_packages():
    """Every non-heavyweight package in requirements.txt must also appear in requirements-core.txt.

    Catches drift where someone adds a dep to requirements.txt but forgets to add it to core.
    """
    core_pkgs = _direct_packages(REQ_CORE)
    for line in REQ_ALL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pkg = re.split(r"[=<>!\[;]", line)[0].strip().lower().replace("-", "_")
        if not pkg or pkg in HEAVYWEIGHT:
            continue
        assert pkg in core_pkgs, (
            f"{pkg!r} is in requirements.txt but missing from requirements-core.txt"
        )


# ---------------------------------------------------------------------------
# Behaviour: matcher degrades gracefully when sentence-transformers is absent
# ---------------------------------------------------------------------------

def test_matcher_degrades_gracefully_without_sentence_transformers(
    monkeypatch, isolated_engine
):
    """SkillMatcherAgent.match() must complete without raising when get_embedding_model()
    raises ImportError (i.e. sentence-transformers not installed).

    The result should still have a valid ats_score — exact/indirect matching still ran.
    """
    import agents.matcher as matcher_module
    from database.models import JobDescription, JobSkill, Skill
    from sqlmodel import Session, select
    from conftest import _seed_user_and_skill

    seed = _seed_user_and_skill(isolated_engine)

    # Create a job with one skill matching the seeded Python skill exactly
    with Session(isolated_engine) as sess:
        job = JobDescription(title="Dev", company="Co", description="needs Python")
        sess.add(job)
        sess.commit()
        sess.refresh(job)

        skill = sess.exec(select(Skill).where(Skill.name == "Python")).first()
        js = JobSkill(job_id=job.job_id, skill_id=skill.skill_id, required=True, weight=1.0)
        sess.add(js)
        sess.commit()
        job_id = job.job_id

    # Simulate sentence-transformers being absent
    def _raise_import_error():
        raise ImportError("No module named 'sentence_transformers'")

    monkeypatch.setattr(matcher_module, "_embedding_model", None)
    monkeypatch.setattr(matcher_module, "get_embedding_model", _raise_import_error)
    monkeypatch.setattr(matcher_module, "engine", isolated_engine)

    agent = matcher_module.SkillMatcherAgent()
    result = agent.match(seed.user_id, job_id)

    assert result.ats_score >= 0.0, "ats_score must be non-negative even without semantic matching"
    assert "Python" in result.matched_skills, \
        "Exact match for Python must still be found when semantic matching is disabled"
