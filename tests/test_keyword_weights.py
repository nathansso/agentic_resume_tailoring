"""Weighted keyword scoring — importance x supportability (issue #125).

The four properties the issue's acceptance criteria name, each pinned here:

1. **An unsupported keyword contributes zero.** The load-bearing test is
   `test_unsupported_keyword_does_not_move_the_score`: it does not merely assert
   the weight is `0.0`, it adds the unsupported keyword to the resume text and
   asserts the *score does not move* — because the claim is about the reward,
   not about a number in a dict, and a weight of zero that still reached the
   numerator would pass a weight assertion and fail the thing that matters.
2. **A title term outweighs a body-prose term.**
3. **Weights are reproducible.** They are persisted and feed a reward (#51
   Phase 2); a map that moves between runs cannot be tuned against — the same
   class of bug as #158.
4. **An absent profile reproduces pre-#125 behavior exactly.** Asserted
   literally, dict-for-dict, the way #137 and #138 asserted their back-compat.

Offline by construction: nothing here reaches a model. The weight computation is
pure, and the graph read is over rows the test seeds itself.
"""
from uuid import uuid4

import pytest
from sqlmodel import Session

import services
from agents import keyword_weights as kw
from agents.ats_scorer import ATSScoringEngine
from database.models import (
    Experience, JDProfile, JobDescription, Project, Skill, User, UserSkill,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

_JD = (
    "Backend Engineer\n"
    "Requirements:\n"
    "- The candidate writes production Python.\n"
    "- The candidate has run Kubernetes clusters.\n"
    "Nice to have:\n"
    "- The candidate has used Terraform.\n"
    "We offer catered lunches and a foosball table.\n"
)


def _payload(**overrides):
    payload = {
        "profile_version": 1,
        "requirements": [
            {"text": "The candidate writes production Python.", "type": "required",
             "criticality": 5, "terms": ["python"],
             "source_section": "Requirements", "confidence": 0.9,
             "ordinal": 0, "edited": False},
            {"text": "The candidate has run Kubernetes clusters.", "type": "required",
             "criticality": 4, "terms": ["kubernetes"],
             "source_section": "Requirements", "confidence": 0.9,
             "ordinal": 1, "edited": False},
            {"text": "The candidate has used Terraform.", "type": "preferred",
             "criticality": 2, "terms": ["terraform"],
             "source_section": "Nice to have", "confidence": 0.8,
             "ordinal": 2, "edited": False},
        ],
        "title_terms": ["backend", "engineer"],
        "role_level": "mid",
    }
    payload.update(overrides)
    return payload


def _support(strong=(), claimed=()):
    return {"strong": sorted(strong), "claimed": sorted(claimed)}


# ── Importance ───────────────────────────────────────────────────────────────

def test_title_term_outweighs_body_prose_term():
    """The issue's second acceptance criterion. `foosball` is in the posting and
    in no requirement; `backend` is in the job title."""
    imp = kw.importance_map(_payload(), _JD)
    assert imp["backend"] > imp["foosball"]
    # And by a wide margin, not a rounding artefact: the title multiplier is the
    # largest structural multiplier in the module.
    assert imp["backend"] >= imp["foosball"] * 5


def test_required_outweighs_preferred_outweighs_unstated():
    imp = kw.importance_map(_payload(), _JD)
    assert imp["python"] > imp["terraform"] > imp["foosball"]


def test_criticality_and_ordinal_order_two_equal_type_requirements():
    """Both `required`, same section; python is crit-5 and first, kubernetes
    crit-4 and second. Position is the signal #121 preserved for exactly this."""
    imp = kw.importance_map(_payload(), _JD)
    assert imp["python"] > imp["kubernetes"]


def test_repetition_raises_importance_without_swamping_type():
    repeated = _JD + ("\nfoosball foosball foosball foosball foosball" * 4)
    imp = kw.importance_map(_payload(), repeated)
    base = kw.importance_map(_payload(), _JD)
    assert imp["foosball"] > base["foosball"]
    # Saturating: a word repeated 20 times still loses to a stated requirement.
    assert imp["foosball"] < imp["terraform"]


def test_importance_universe_is_the_scorers_own_token_set():
    """A weight that keyed on anything else could never line up with the keyword
    it is meant to weigh."""
    imp = kw.importance_map(_payload(), _JD)
    assert set(imp) == ATSScoringEngine._extract_keywords(_JD)


def test_empty_profile_still_produces_a_map():
    """Extraction can fail or return nothing; the map must still cover the JD."""
    imp = kw.importance_map({}, _JD)
    assert set(imp) == ATSScoringEngine._extract_keywords(_JD)
    assert set(imp.values()) != {0.0}


# ── Supportability ───────────────────────────────────────────────────────────

def test_three_tiers():
    support = _support(strong=["python"], claimed=["kubernetes"])
    tiers = kw.supportability_map(["python", "kubernetes", "terraform"], support)
    assert tiers == {
        "python": kw.SUPPORT_STRONG,
        "kubernetes": kw.SUPPORT_ADJACENT,
        "terraform": kw.SUPPORT_NONE,
    }


def test_naming_variant_is_adjacent_not_strong():
    """`pytorch` against evidenced `torch`, `postgresql` against `postgres`:
    related tech earns partial credit — the tier where reframe/keyword_weave
    have positive expected value — never full credit."""
    tiers = kw.supportability_map(
        ["pytorch", "postgresql", "haskell"],
        _support(strong=["torch", "postgres"]),
    )
    assert tiers["pytorch"] == kw.SUPPORT_ADJACENT
    assert tiers["postgresql"] == kw.SUPPORT_ADJACENT
    assert tiers["haskell"] == kw.SUPPORT_NONE


def test_evidenced_token_is_never_demoted_to_adjacent():
    tiers = kw.supportability_map(["python"], _support(strong=["python"],
                                                       claimed=["python"]))
    assert tiers["python"] == kw.SUPPORT_STRONG


def test_no_evidence_at_all_tiers_everything_none():
    tiers = kw.supportability_map(["python", "kubernetes"], {})
    assert set(tiers.values()) == {kw.SUPPORT_NONE}


# ── The product ──────────────────────────────────────────────────────────────

def test_unsupported_term_weighs_exactly_zero():
    blob = kw.compute_weights(_payload(), _JD, _support(strong=["python"]))
    assert blob["terms"]["terraform"] == 0.0
    # Its importance is not zero — the zero comes from supportability alone,
    # which is what makes the rule "never pay for fabrication" and not
    # "this term does not matter".
    assert blob["importance"]["terraform"] > 0.0


def test_adjacent_term_weighs_half_of_the_same_term_when_strong():
    strong = kw.compute_weights(_payload(), _JD, _support(strong=["kubernetes"]))
    adjacent = kw.compute_weights(_payload(), _JD, _support(claimed=["kubernetes"]))
    # abs tolerance, not relative: stored weights are rounded for inspectability.
    assert adjacent["terms"]["kubernetes"] == pytest.approx(
        strong["terms"]["kubernetes"] / 2, abs=1e-6)


def test_weights_are_stable_across_runs_for_a_fixed_profile():
    """The issue's third acceptance criterion. Inputs are re-shuffled between
    the two computations: any aggregation that depended on iteration order —
    the #158 failure class — shows up here."""
    support = _support(strong=["python", "backend", "engineer"],
                       claimed=["kubernetes"])
    shuffled = {"strong": list(reversed(support["strong"])),
                "claimed": list(support["claimed"])}
    reordered = _payload()
    reordered["title_terms"] = list(reversed(reordered["title_terms"]))

    first = kw.compute_weights(_payload(), _JD, support)
    second = kw.compute_weights(reordered, _JD, shuffled)
    assert kw.weights_digest(first) == kw.weights_digest(second)


def test_weights_blob_carries_the_jd_digest_it_was_computed_over():
    a = kw.compute_weights(_payload(), _JD, _support(strong=["python"]))
    b = kw.compute_weights(_payload(), _JD + " Also Rust.", _support(strong=["python"]))
    assert a["jd_digest"] != b["jd_digest"]


# ── The scorer ───────────────────────────────────────────────────────────────

_RESUME = "Skills: Python, Docker\nBackend Engineer at Acme\nBuilt production Python services."


def test_absent_weights_are_byte_for_byte_the_pre_125_score():
    """#137 and #138 both asserted their back-compat literally; so does this.
    Every job analyzed before #121 shipped, and every job whose extraction
    failed, takes this path."""
    plain = ATSScoringEngine._keyword_coverage(_RESUME, _JD)
    for absent in (None, {}):
        assert ATSScoringEngine._keyword_coverage(_RESUME, _JD, absent) == plain
    # And the pre-#125 shape gains no keys.
    assert set(plain) == {"score", "matched_keywords", "missing_keywords", "total"}


def test_unsupported_keyword_does_not_move_the_score():
    """The issue's central claim, asserted on the *reward* rather than on a
    weight: stapling an unsupported keyword onto the resume must pay nothing."""
    weights = kw.compute_weights(
        _payload(), _JD, _support(strong=["python", "backend", "engineer"]),
    )["terms"]
    before = ATSScoringEngine._keyword_coverage(_RESUME, _JD, weights)
    stuffed = _RESUME + "\nTerraform. Foosball. Catered lunches."
    after = ATSScoringEngine._keyword_coverage(stuffed, _JD, weights)

    assert after["score"] == before["score"]
    # The terms genuinely landed in the text — this is a weighting result, not
    # a matching accident.
    assert "terraform" in after["matched_keywords"]
    assert "terraform" not in before["matched_keywords"]
    assert "terraform" in after["unsupported_keywords"]


def test_a_supported_keyword_still_moves_the_score():
    """The control. Without this, a weighting bug that zeroed everything would
    pass the test above."""
    weights = kw.compute_weights(
        _payload(), _JD, _support(strong=["python", "kubernetes"]),
    )["terms"]
    before = ATSScoringEngine._keyword_coverage(_RESUME, _JD, weights)
    after = ATSScoringEngine._keyword_coverage(
        _RESUME + "\nRan Kubernetes clusters in production.", _JD, weights)
    assert after["score"] > before["score"]


def test_all_terms_unsupported_falls_back_to_uniform_not_zero():
    """A candidate with an empty graph. `0/0` is not a score, and reporting 0.0
    would claim the resume covers nothing when what actually happened is that
    there is no supportability information at all."""
    weights = kw.compute_weights(_payload(), _JD, _support())["terms"]
    assert set(weights.values()) == {0.0}
    scored = ATSScoringEngine._keyword_coverage(_RESUME, _JD, weights)
    assert scored == ATSScoringEngine._keyword_coverage(_RESUME, _JD)


def test_weighted_score_is_still_monotone_in_text():
    """#137 established the composite is monotone non-decreasing in text.
    Weighting does not overturn that — weights are fixed per term and do not
    depend on the resume — it makes the gradient *zero* in the unsupported
    direction instead of positive. Both halves are pinned so a later change
    cannot quietly flip either."""
    weights = kw.compute_weights(
        _payload(), _JD, _support(strong=["python", "kubernetes", "backend"]),
    )["terms"]
    base = ATSScoringEngine._keyword_coverage(_RESUME, _JD, weights)["score"]
    for addition in ("Terraform", "Kubernetes", "foosball", "Python Kubernetes"):
        grown = ATSScoringEngine._keyword_coverage(
            _RESUME + "\n" + addition, _JD, weights)["score"]
        assert grown >= base


def test_score_tailored_accepts_weights_and_defaults_to_uniform():
    content = {
        "experiences": [{"title": "Backend Engineer", "company": "Acme",
                         "bullets": ["Built production Python services."]}],
        "projects": [{"name": "Toolkit", "bullets": ["Python CLI."]}],
    }
    plain = ATSScoringEngine.score_tailored(content, _JD, matched_skills={})
    assert "weighted" not in plain["keyword_coverage"]

    weights = kw.compute_weights(_payload(), _JD, _support(strong=["python"]))["terms"]
    weighted = ATSScoringEngine.score_tailored(
        content, _JD, matched_skills={}, keyword_weights=weights)
    assert weighted["keyword_coverage"]["weighted"] is True


# ── Supportability from the knowledge graph ──────────────────────────────────

def _seed_candidate(engine, *, skills, project=None, experience=None):
    with Session(engine) as s:
        user = User(name="Alice", email=f"a_{uuid4().hex[:8]}@example.com")
        s.add(user)
        s.commit()
        s.refresh(user)
        uid = user.user_id

        for name in skills:
            skill = Skill(name=name, category="Tool")
            s.add(skill)
            s.commit()
            s.refresh(skill)
            s.add(UserSkill(user_id=uid, skill_id=skill.skill_id, proficiency=4,
                            evidence_source="resume", confidence_score=0.9))
        if project:
            s.add(Project(user_id=uid, name=project[0], description=project[1]))
        if experience:
            s.add(Experience(user_id=uid, title=experience[0],
                             company=experience[1], description=experience[2],
                             bullets=list(experience[3])))
        s.commit()
    return uid


def test_support_index_splits_backed_skills_from_bare_claims(isolated_engine):
    """The issue's own table: a skill with project/experience backing is full
    credit; a skill the user claims with nothing behind it is partial."""
    uid = _seed_candidate(
        isolated_engine,
        skills=["Python", "Kubernetes"],
        project=("Search Service", "A Python search service"),
    )
    index = services.build_support_index(uid)
    assert "python" in index["strong"]
    assert "kubernetes" in index["claimed"]
    assert "kubernetes" not in index["strong"]


def test_support_index_includes_experience_bullet_vocabulary(isolated_engine):
    """Words already in the candidate's logged work are restating, not
    inventing, so they are supportable even when no Skill row names them."""
    uid = _seed_candidate(
        isolated_engine,
        skills=["Python"],
        experience=("Backend Engineer", "Acme", "Services",
                    ["Deployed services with Terraform to AWS."]),
    )
    index = services.build_support_index(uid)
    assert "terraform" in index["strong"]


def test_support_index_is_sorted_and_stable(isolated_engine):
    uid = _seed_candidate(
        isolated_engine, skills=["Python", "Kafka", "Docker"],
        project=("Pipeline", "Kafka and Docker pipeline in Python"),
    )
    first = services.build_support_index(uid)
    second = services.build_support_index(uid)
    assert first == second
    assert first["strong"] == sorted(first["strong"])
    assert first["claimed"] == sorted(first["claimed"])


def test_support_index_degrades_to_empty_for_an_unknown_user(isolated_engine):
    assert services.build_support_index(uuid4()) == {"strong": [], "claimed": []}


# ── Resolution and persistence ───────────────────────────────────────────────

def _seed_job_with_profile(engine, uid, description=_JD, payload=None):
    with Session(engine) as s:
        job = JobDescription(title="Backend Engineer", company="Acme",
                             description=description, status="created", user_id=uid)
        s.add(job)
        s.commit()
        s.refresh(job)
        jid = job.job_id
        if payload is not None:
            s.add(JDProfile(job_id=jid, user_id=uid, payload=payload,
                            payload_hash="h", extraction_key="k",
                            extraction_version=1, role_level="mid"))
            s.commit()
    return jid


def test_resolve_returns_none_without_a_profile(isolated_engine):
    """The pre-#121 job. None is what makes the scorer's fallback the exact old
    behavior rather than an approximation of it."""
    uid = _seed_candidate(isolated_engine, skills=["Python"])
    jid = _seed_job_with_profile(isolated_engine, uid, payload=None)
    assert services.resolve_keyword_weights(jid, uid, _JD) is None


def test_resolve_persists_the_weights_onto_the_profile(isolated_engine):
    """#121 left `JDProfile.weights` empty for this issue to fill."""
    uid = _seed_candidate(isolated_engine, skills=["Python"],
                          project=("Svc", "A Python service"))
    jid = _seed_job_with_profile(isolated_engine, uid, payload=_payload())

    terms = services.resolve_keyword_weights(jid, uid, _JD)
    assert terms["python"] > 0
    assert terms["terraform"] == 0.0

    with Session(isolated_engine) as s:
        stored = s.exec(
            services.select(JDProfile).where(JDProfile.job_id == jid)
        ).first()
        blob = stored.weights
        assert blob["terms"] == terms
        assert blob["version"] == kw.WEIGHTS_VERSION
        assert blob["supportability"]["python"] == kw.SUPPORT_STRONG


def test_resolve_is_reproducible_across_calls(isolated_engine):
    uid = _seed_candidate(isolated_engine, skills=["Python", "Kafka"],
                          project=("Svc", "A Python and Kafka service"))
    jid = _seed_job_with_profile(isolated_engine, uid, payload=_payload())
    assert services.resolve_keyword_weights(jid, uid, _JD) == \
        services.resolve_keyword_weights(jid, uid, _JD)


def test_resolve_tracks_a_newly_evidenced_skill(isolated_engine):
    """Weights are recomputed rather than cached precisely so this cannot go
    stale in the direction that matters: a term that has just become
    supportable must stop weighing zero."""
    uid = _seed_candidate(isolated_engine, skills=["Python"])
    jid = _seed_job_with_profile(isolated_engine, uid, payload=_payload())
    assert services.resolve_keyword_weights(jid, uid, _JD)["terraform"] == 0.0

    with Session(isolated_engine) as s:
        s.add(Project(user_id=uid, name="Infra", description="Terraform modules"))
        s.commit()
    assert services.resolve_keyword_weights(jid, uid, _JD)["terraform"] > 0.0


def test_resolve_without_persist_writes_nothing(isolated_engine):
    """`plan_preview` guarantees a preview performs no DB writes (issue #91)."""
    uid = _seed_candidate(isolated_engine, skills=["Python"],
                          project=("Svc", "A Python service"))
    jid = _seed_job_with_profile(isolated_engine, uid, payload=_payload())

    terms = services.resolve_keyword_weights(jid, uid, _JD, persist=False)
    assert terms["python"] > 0
    with Session(isolated_engine) as s:
        stored = s.exec(
            services.select(JDProfile).where(JDProfile.job_id == jid)
        ).first()
        assert not stored.weights


def test_score_resolves_weights_and_falls_back_when_there_is_no_profile(isolated_engine):
    """End to end through `ATSScoringEngine.score`, both branches."""
    uid = _seed_candidate(isolated_engine, skills=["Python"],
                          experience=("Backend Engineer", "Acme", "Services",
                                      ["Wrote production Python."]))
    unprofiled = _seed_job_with_profile(isolated_engine, uid, payload=None)
    profiled = _seed_job_with_profile(isolated_engine, uid, payload=_payload())

    engine_ = ATSScoringEngine()
    with Session(isolated_engine) as s:
        plain = engine_.score(uid, unprofiled, s, 50.0)
        weighted = engine_.score(uid, profiled, s, 50.0)

    assert "weighted" not in plain["keyword_coverage"]
    assert weighted["keyword_coverage"]["weighted"] is True
    assert "terraform" in weighted["keyword_coverage"]["unsupported_keywords"]
