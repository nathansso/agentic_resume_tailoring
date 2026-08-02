"""JD profile extraction, persistence, and correction (issue #121).

Covers the three properties the profile is built on:

1. **Determinism is enforced by the cache, not by the model.** The load-bearing
   test is `test_second_rebuild_performs_no_extraction`: it counts extractor
   invocations, because "two runs produce byte-identical profiles" is only true
   if the second run never reaches a model at all.
2. **Source order survives.** `requirements[]` is stored in the posting's order
   and each row carries its `ordinal`. Ordinal position is one of #125's
   importance signals and the only one that cannot be recovered later, so it is
   pinned here rather than left to be noticed when #125 reads garbage.
3. **A human correction is never silently overwritten.** An `edited`
   requirement survives a re-extraction, including one that no longer matches
   anything the model produced.

Offline by construction: the single LLM call goes through the #142
`get_extractor` seam and is scripted with the same `_Scripted` stand-in
`tests/test_job_card.py` and `tests/test_knowledge_extraction.py` use.
"""
from uuid import UUID, uuid4

import pytest
from sqlmodel import Session

import agents.jd_profile as jdp
from agents.extraction_schemas import (
    JDProfileExtraction, JDRequirementItem, RequirementType,
)
from database.models import JDProfile, JobDescription, User


class _Scripted:
    """A StructuredExtractor stand-in returning a fixed validated model."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        return self.payload


class _Failing:
    def __init__(self):
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        raise RuntimeError("provider is down")


def _req(text, rtype=RequirementType.REQUIRED, criticality=3, terms=None,
         section=None, confidence=None):
    return JDRequirementItem(
        text=text, type=rtype, criticality=criticality,
        terms=terms or [], source_section=section, confidence=confidence,
    )


def _extraction(*requirements, title_terms=None):
    return JDProfileExtraction(
        requirements=list(requirements), title_terms=title_terms or [])


_DEFAULT = _extraction(
    _req("The candidate has 5 years of Python.", terms=["Python"],
         section="Required Qualifications", criticality=5),
    _req("The candidate has used Kafka.", RequirementType.PREFERRED,
         terms=["Kafka"], section="Nice to Have", criticality=2),
    _req("The team works in a hybrid office.", RequirementType.INCIDENTAL,
         criticality=1),
)


def _script(monkeypatch, extraction=_DEFAULT):
    """Patch the extraction seam; return the scripted stand-in for call counts."""
    scripted = _Scripted(extraction)
    monkeypatch.setattr(jdp, "get_extractor", lambda **kw: scripted)
    return scripted


def _seed_user(engine) -> User:
    """Seeded in its own session: a later commit in a shared session expires
    this instance, and the router fixture reads it after the session closes."""
    with Session(engine) as s:
        user = User(name="Alice", email=f"a_{uuid4().hex[:8]}@example.com")
        s.add(user)
        s.commit()
        s.refresh(user)
        s.expunge(user)
    return user


def _seed_job(engine, description="We need 5+ years of Python. Kafka a plus.",
              title="Senior Machine Learning Engineer"):
    user = _seed_user(engine)
    with Session(engine) as s:
        job = JobDescription(
            title=title, company="Acme", description=description,
            status="created", user_id=user.user_id,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
    return user, job


# ── Compile: deterministic normalization ─────────────────────────────────────

def test_compile_is_deterministic():
    a = jdp.compile_profile_payload("SWE", "Python role", _DEFAULT)
    b = jdp.compile_profile_payload("SWE", "Python role", _DEFAULT)
    assert a == b
    assert jdp.payload_digest(a) == jdp.payload_digest(b)


def test_compile_preserves_source_order_and_numbers_it():
    """The #125 signal that cannot be recovered later. Types are deliberately
    interleaved so any grouping or sorting shows up as a failure."""
    extraction = _extraction(
        _req("Preferred first.", RequirementType.PREFERRED),
        _req("Required second.", RequirementType.REQUIRED),
        _req("Incidental third.", RequirementType.INCIDENTAL),
        _req("Required fourth.", RequirementType.REQUIRED),
    )
    payload = jdp.compile_profile_payload("SWE", "jd", extraction)
    assert [r["text"] for r in payload["requirements"]] == [
        "Preferred first.", "Required second.",
        "Incidental third.", "Required fourth.",
    ]
    assert [r["ordinal"] for r in payload["requirements"]] == [0, 1, 2, 3]


def test_iter_requirements_filters_without_reordering():
    payload = jdp.compile_profile_payload("SWE", "jd", _extraction(
        _req("Preferred first.", RequirementType.PREFERRED),
        _req("Required second.", RequirementType.REQUIRED),
        _req("Required third.", RequirementType.REQUIRED),
    ))
    required = jdp.iter_requirements(payload, types=["required"])
    assert [r["ordinal"] for r in required] == [1, 2]


def test_compile_clamps_out_of_range_values():
    """Criticality is unconstrained in the schema on purpose — a range-bound
    field would turn one bad integer into a lost profile."""
    payload = jdp.compile_profile_payload("SWE", "jd", _extraction(
        _req("High.", criticality=99, confidence=4.2),
        _req("Low.", criticality=-3, confidence=-1.0),
        _req("Absent.", criticality=None),
    ))
    assert [r["criticality"] for r in payload["requirements"]] == [5, 1, 3]
    assert [r["confidence"] for r in payload["requirements"]] == [1.0, 0.0, None]


def test_clamp_helpers_survive_unparseable_input():
    """Pydantic already rejects a non-numeric criticality on the extraction
    path, so this branch exists for the edit API, which takes raw JSON."""
    assert jdp._clamp_criticality("not a number") == 3
    assert jdp._clamp_criticality(None) == 3
    assert jdp._clamp_confidence("nope") is None


def test_compile_drops_untexted_requirements_and_renumbers():
    payload = jdp.compile_profile_payload("SWE", "jd", _extraction(
        _req("Kept."), _req("   "), _req(None), _req("Also kept."),
    ))
    assert [r["text"] for r in payload["requirements"]] == ["Kept.", "Also kept."]
    assert [r["ordinal"] for r in payload["requirements"]] == [0, 1]


def test_compile_normalizes_terms_order_preserving():
    payload = jdp.compile_profile_payload("SWE", "jd", _extraction(
        _req("x", terms=["PyTorch", "  ", "pytorch", "CUDA"]),
    ))
    assert payload["requirements"][0]["terms"] == ["pytorch", "cuda"]


def test_title_terms_fall_back_to_the_title_when_model_omits_them():
    """The highest-signal importance input (#125) must never be empty just
    because the model skipped the field."""
    payload = jdp.compile_profile_payload(
        "Senior Machine Learning Engineer", "jd", _extraction(_req("x")))
    assert payload["title_terms"] == ["engineer", "learning", "machine", "senior"]


def test_role_level_matches_the_scorers_own_detector():
    from agents.ats_scorer import _detect_level
    jd = "We are hiring a senior engineer with 7+ years."
    payload = jdp.compile_profile_payload("Eng", jd, _extraction(_req("x")))
    assert payload["role_level"] == _detect_level(jd) == "senior"


def test_compile_of_none_extraction_is_empty_but_well_formed():
    payload = jdp.compile_profile_payload("SWE", "jd", None)
    assert payload["requirements"] == []
    assert payload["profile_version"] == jdp.PROFILE_VERSION


# ── Extraction key ───────────────────────────────────────────────────────────

def test_extraction_key_ignores_whitespace_but_not_content():
    assert jdp.extraction_key("a  b\n c") == jdp.extraction_key("a b c")
    assert jdp.extraction_key("a b") != jdp.extraction_key("a c")


def test_extraction_key_is_version_scoped():
    assert jdp.extraction_key("jd", version=1) != jdp.extraction_key("jd", version=2)


# ── The cache guarantee ──────────────────────────────────────────────────────

def test_second_rebuild_performs_no_extraction(isolated_engine, monkeypatch):
    """Acceptance criteria 1 and 5 are the same criterion, and this is it.

    An LLM call is not byte-stable, so "two runs produce byte-identical
    profiles" can only be enforced by the second run never reaching a model.
    """
    import services
    _, job = _seed_job(isolated_engine)
    scripted = _script(monkeypatch)

    first = services.rebuild_jd_profile(job.job_id, user_id=job.user_id)
    assert first is not None
    assert scripted.calls == 1

    second = services.rebuild_jd_profile(job.job_id, user_id=job.user_id)
    assert second == first
    assert scripted.calls == 1, "second rebuild re-extracted"

    with Session(isolated_engine) as s:
        rows = s.exec(
            __import__("sqlmodel").select(JDProfile).where(JDProfile.job_id == job.job_id)
        ).all()
    assert len(rows) == 1


def test_changed_jd_text_re_extracts(isolated_engine, monkeypatch):
    import services
    _, job = _seed_job(isolated_engine)
    scripted = _script(monkeypatch)
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id)

    with Session(isolated_engine) as s:
        row = s.get(JobDescription, job.job_id)
        row.description = "Completely different posting about Rust."
        s.add(row)
        s.commit()

    services.rebuild_jd_profile(job.job_id, user_id=job.user_id)
    assert scripted.calls == 2


def test_force_re_extracts_on_a_cache_hit(isolated_engine, monkeypatch):
    import services
    _, job = _seed_job(isolated_engine)
    scripted = _script(monkeypatch)
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id)
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id, force=True)
    assert scripted.calls == 2


def test_version_bump_invalidates_stored_profiles(isolated_engine, monkeypatch):
    import services
    _, job = _seed_job(isolated_engine)
    scripted = _script(monkeypatch)
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id)
    assert scripted.calls == 1

    monkeypatch.setattr(jdp, "PROFILE_VERSION", jdp.PROFILE_VERSION + 1)
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id)
    assert scripted.calls == 2


# ── Graceful degradation ─────────────────────────────────────────────────────

def test_extraction_failure_writes_no_row(isolated_engine, monkeypatch):
    """An absent profile reproduces pre-#121 behavior, so a failed extraction
    must leave nothing behind rather than persist an empty profile."""
    import services
    _, job = _seed_job(isolated_engine)
    failing = _Failing()
    monkeypatch.setattr(jdp, "get_extractor", lambda **kw: failing)

    assert services.rebuild_jd_profile(job.job_id, user_id=job.user_id) is None
    assert services.load_jd_profile(job.job_id) is None


def test_extraction_failure_keeps_the_existing_profile(isolated_engine, monkeypatch):
    import services
    _, job = _seed_job(isolated_engine)
    _script(monkeypatch)
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id)
    before = services.load_jd_profile(job.job_id)

    monkeypatch.setattr(jdp, "get_extractor", lambda **kw: _Failing())
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id, force=True)

    assert services.load_jd_profile(job.job_id)["payload"] == before["payload"]


def test_job_without_description_extracts_nothing(isolated_engine, monkeypatch):
    import services
    _, job = _seed_job(isolated_engine, description="")
    scripted = _script(monkeypatch)
    assert services.rebuild_jd_profile(job.job_id, user_id=job.user_id) is None
    assert scripted.calls == 0


def test_read_path_tolerates_absent_and_malformed_payloads():
    assert jdp.iter_requirements(None) == []
    assert jdp.iter_requirements({}) == []
    assert jdp.profile_terms(None) == []
    assert jdp.iter_requirements({"requirements": ["not a dict", None]}) == []


# ── Edit preservation ────────────────────────────────────────────────────────

def test_merge_without_edits_returns_the_fresh_payload():
    old = jdp.compile_profile_payload("SWE", "jd", _DEFAULT)
    new = jdp.compile_profile_payload("SWE", "jd2", _extraction(_req("Fresh.")))
    assert jdp.merge_edits(old, new) == new


def test_merge_carries_an_edited_requirement_and_refreshes_the_rest():
    old = jdp.compile_profile_payload("SWE", "jd", _extraction(
        _req("Corrected by hand.", RequirementType.REQUIRED),
        _req("Stale.", RequirementType.REQUIRED),
    ))
    old["requirements"][0]["type"] = "preferred"
    old["requirements"][0]["edited"] = True

    new = jdp.compile_profile_payload("SWE", "jd", _extraction(
        _req("Corrected by hand.", RequirementType.REQUIRED),
        _req("Refreshed.", RequirementType.REQUIRED),
    ))
    merged = jdp.merge_edits(old, new)

    assert merged["requirements"][0]["type"] == "preferred"
    assert merged["requirements"][0]["edited"] is True
    assert merged["requirements"][1]["text"] == "Refreshed."


def test_merge_keeps_an_edited_requirement_the_model_no_longer_produces():
    """Dropping it would silently discard the user's work — the exact failure
    the edit flag exists to prevent."""
    old = jdp.compile_profile_payload("SWE", "jd", _extraction(
        _req("Only the human saw this."),
    ))
    old["requirements"][0]["edited"] = True

    new = jdp.compile_profile_payload("SWE", "jd", _extraction(_req("Something else.")))
    merged = jdp.merge_edits(old, new)

    texts = [r["text"] for r in merged["requirements"]]
    assert "Only the human saw this." in texts
    assert "Something else." in texts
    assert [r["ordinal"] for r in merged["requirements"]] == [0, 1]


def test_merge_matches_by_text_when_ordinals_shift():
    old = jdp.compile_profile_payload("SWE", "jd", _extraction(
        _req("First."), _req("Moved."),
    ))
    old["requirements"][1]["criticality"] = 5
    old["requirements"][1]["edited"] = True

    new = jdp.compile_profile_payload("SWE", "jd", _extraction(
        _req("Inserted."), _req("First."), _req("Moved."),
    ))
    merged = jdp.merge_edits(old, new)
    moved = [r for r in merged["requirements"] if r["text"] == "Moved."]
    assert len(moved) == 1
    assert moved[0]["criticality"] == 5 and moved[0]["edited"] is True


# ── Edit API surface ─────────────────────────────────────────────────────────

def test_edit_marks_only_touched_requirements(isolated_engine, monkeypatch):
    import services
    _, job = _seed_job(isolated_engine)
    _script(monkeypatch)
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id)

    updated = services.update_jd_profile_requirements(
        job.job_id, [{"ordinal": 1, "type": "required", "criticality": 5}])
    reqs = updated["payload"]["requirements"]

    assert reqs[1]["type"] == "required" and reqs[1]["criticality"] == 5
    assert reqs[1]["edited"] is True
    assert reqs[0]["edited"] is False and reqs[2]["edited"] is False


def test_edit_ignores_unknown_ordinals_fields_and_invalid_types(isolated_engine, monkeypatch):
    import services
    _, job = _seed_job(isolated_engine)
    _script(monkeypatch)
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id)
    before = services.load_jd_profile(job.job_id)["payload"]

    updated = services.update_jd_profile_requirements(job.job_id, [
        {"ordinal": 99, "type": "required"},
        {"ordinal": 0, "type": "not-a-type"},
        {"ordinal": 0, "profile_version": 7},
    ])
    assert updated["payload"] == before


def test_edit_cannot_reorder_requirements(isolated_engine, monkeypatch):
    """`ordinal` is the key, never a target: reordering would destroy the
    source-order signal #125 reads."""
    import services
    _, job = _seed_job(isolated_engine)
    _script(monkeypatch)
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id)

    updated = services.update_jd_profile_requirements(
        job.job_id, [{"ordinal": 0, "criticality": 4}])
    assert [r["ordinal"] for r in updated["payload"]["requirements"]] == [0, 1, 2]


def test_correction_survives_a_later_re_extraction(isolated_engine, monkeypatch):
    """The end-to-end version of the guarantee: fix a mis-parsed requirement,
    then re-extract, and the fix is still there."""
    import services
    _, job = _seed_job(isolated_engine)
    _script(monkeypatch)
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id)
    services.update_jd_profile_requirements(
        job.job_id, [{"ordinal": 0, "type": "preferred"}])

    services.rebuild_jd_profile(job.job_id, user_id=job.user_id, force=True)

    reqs = services.load_jd_profile(job.job_id)["payload"]["requirements"]
    assert reqs[0]["type"] == "preferred"
    assert reqs[0]["edited"] is True


def test_rewritten_text_survives_re_extraction_without_duplicating(
    isolated_engine, monkeypatch,
):
    """The hardest merge case: the user rewrote the requirement's own text, so
    it no longer matches what the model produces. `original_text` is what keeps
    it a match instead of appending the correction beside the original."""
    import services
    _, job = _seed_job(isolated_engine)
    _script(monkeypatch)
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id)

    services.update_jd_profile_requirements(
        job.job_id,
        [{"ordinal": 0, "text": "The candidate has 5 years of Python and Django."}],
    )
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id, force=True)

    reqs = services.load_jd_profile(job.job_id)["payload"]["requirements"]
    texts = [r["text"] for r in reqs]
    assert texts.count("The candidate has 5 years of Python and Django.") == 1
    assert "The candidate has 5 years of Python." not in texts
    assert len(reqs) == 3


def test_editing_text_to_the_same_value_is_not_an_edit(isolated_engine, monkeypatch):
    import services
    _, job = _seed_job(isolated_engine)
    _script(monkeypatch)
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id)
    original = services.load_jd_profile(job.job_id)["payload"]["requirements"][0]["text"]

    updated = services.update_jd_profile_requirements(
        job.job_id, [{"ordinal": 0, "text": original}])
    req = updated["payload"]["requirements"][0]
    assert req["edited"] is False
    assert "original_text" not in req


def test_edit_on_a_job_without_a_profile_returns_none(isolated_engine):
    import services
    _, job = _seed_job(isolated_engine)
    assert services.update_jd_profile_requirements(job.job_id, [{"ordinal": 0}]) is None


# ── Backfill and lifecycle ───────────────────────────────────────────────────

def test_load_backfills_only_when_asked(isolated_engine, monkeypatch):
    """Pre-#121 jobs have no profile and would otherwise never gain one, but
    backfill can spend an LLM call, so it is opt-in."""
    import services
    _, job = _seed_job(isolated_engine)
    scripted = _script(monkeypatch)

    assert services.load_jd_profile(job.job_id) is None
    assert scripted.calls == 0

    profile = services.load_jd_profile(job.job_id, backfill=True)
    assert profile is not None and scripted.calls == 1
    assert len(profile["payload"]["requirements"]) == 3


def test_deleting_a_job_removes_its_profile(isolated_engine, monkeypatch):
    """JDProfile carries a real FK to jobdescription, which Postgres enforces
    and SQLite does not — an uncleaned row fails only in production."""
    import services
    from sqlmodel import select
    _, job = _seed_job(isolated_engine)
    _script(monkeypatch)
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id)

    assert services.delete_job(str(job.job_id)) == "Job deleted."
    with Session(isolated_engine) as s:
        assert s.exec(select(JDProfile).where(JDProfile.job_id == job.job_id)).all() == []


def test_deleting_a_job_removes_its_job_card(isolated_engine):
    """Same FK hazard, pre-existing since #137 and fixed alongside."""
    import services
    from sqlmodel import select
    from database.models import JobCard
    _, job = _seed_job(isolated_engine)
    with Session(isolated_engine) as s:
        s.add(JobCard(user_id=job.user_id, job_id=job.job_id, payload={}))
        s.commit()

    assert services.delete_job(str(job.job_id)) == "Job deleted."
    with Session(isolated_engine) as s:
        assert s.exec(select(JobCard).where(JobCard.job_id == job.job_id)).all() == []


# ── Router ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def profile_client(isolated_engine, monkeypatch):
    import database.db as db_module
    monkeypatch.setattr(db_module, "engine", isolated_engine)
    import web.routers.jobs_router as jobs_router_module
    monkeypatch.setattr(jobs_router_module, "engine", isolated_engine)

    from fastapi.testclient import TestClient
    from web.app import create_app
    import web.auth as web_auth_module

    def _make(user: User) -> TestClient:
        app = create_app()
        app.dependency_overrides[web_auth_module.get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=True)

    return _make


def test_get_profile_404s_before_extraction(isolated_engine, profile_client):
    user, job = _seed_job(isolated_engine)
    resp = profile_client(user).get(f"/api/jobs/{job.job_id}/profile")
    assert resp.status_code == 404


def test_get_profile_returns_the_requirements(isolated_engine, profile_client, monkeypatch):
    import services
    user, job = _seed_job(isolated_engine)
    _script(monkeypatch)
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id)

    resp = profile_client(user).get(f"/api/jobs/{job.job_id}/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["payload"]["requirements"]) == 3
    assert body["role_level"] == "senior"


def test_put_profile_marks_the_edit(isolated_engine, profile_client, monkeypatch):
    import services
    user, job = _seed_job(isolated_engine)
    _script(monkeypatch)
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id)

    resp = profile_client(user).put(
        f"/api/jobs/{job.job_id}/profile",
        json={"requirements": [{"ordinal": 0, "criticality": 1}]},
    )
    assert resp.status_code == 200
    reqs = resp.json()["payload"]["requirements"]
    assert reqs[0]["criticality"] == 1 and reqs[0]["edited"] is True


def test_profile_is_owner_scoped(isolated_engine, profile_client, monkeypatch):
    """Issue #73: a JD profile is derived from user-supplied text."""
    import services
    _, job = _seed_job(isolated_engine)
    _script(monkeypatch)
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id)

    with Session(isolated_engine) as s:
        mallory = User(name="Mallory", email=f"m_{uuid4().hex[:8]}@example.com")
        s.add(mallory)
        s.commit()
        s.refresh(mallory)

    client = profile_client(mallory)
    assert client.get(f"/api/jobs/{job.job_id}/profile").status_code == 403
    assert client.put(
        f"/api/jobs/{job.job_id}/profile",
        json={"requirements": [{"ordinal": 0, "criticality": 1}]},
    ).status_code == 403


def test_reextract_endpoint_forces_a_fresh_extraction(isolated_engine, profile_client, monkeypatch):
    import services
    user, job = _seed_job(isolated_engine)
    scripted = _script(monkeypatch)
    services.rebuild_jd_profile(job.job_id, user_id=job.user_id)

    resp = profile_client(user).post(f"/api/jobs/{job.job_id}/profile/reextract")
    assert resp.status_code == 200
    assert scripted.calls == 2
