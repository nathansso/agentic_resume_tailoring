"""Preference-vs-JD arbitration (issue #129).

The precedence chain is the core of the issue, so it gets its own file. Every
test here is against the pure `compile_constraints` — no database, no LLM, no
clock — which is what makes "deterministic given (PreferenceProfile, JDProfile,
KG)" an assertion rather than an aspiration.
"""
import pytest

from agents.arbitration import (
    REASON_UNSUPPORTED, compile_constraints, is_empty, matching_requirements,
    render_constraints,
)


def _pref(**kwargs):
    base = {
        "preference_id": "p1",
        "text": "Do not mention machine learning.",
        "polarity": "suppress",
        "target_type": "skill",
        "target_key": "skill:machine learning",
        "target_term": "machine learning",
        "strength": 3,
        "status": "active",
    }
    base.update(kwargs)
    return base


def _jd(criticality=3, terms=("machine learning",), rtype="required"):
    return {
        "requirements": [{
            "text": "The candidate has machine learning experience.",
            "type": rtype,
            "criticality": criticality,
            "terms": list(terms),
            "ordinal": 0,
        }],
    }


# ── the empty case ───────────────────────────────────────────────────────────

def test_no_preferences_compiles_an_empty_constraint_set():
    constraints = compile_constraints([], _jd(), ["skill:python"])
    assert constraints == {"applied": [], "conflicts": [], "refused": []}
    assert is_empty(constraints)
    assert render_constraints(constraints) == ""


def test_a_preference_with_no_jd_profile_still_applies():
    """An unextracted JD cannot contest anything — that is correct, not a
    degradation. The user's stated preference is the only signal present."""
    constraints = compile_constraints([_pref()], None, [])
    assert [a["preference_id"] for a in constraints["applied"]] == ["p1"]
    assert constraints["conflicts"] == []


# ── 1. truthfulness first ────────────────────────────────────────────────────

def test_an_emphasize_with_no_kg_support_is_refused():
    pref = _pref(polarity="emphasize", target_key=None, target_term="leadership")
    constraints = compile_constraints([pref], _jd(), ["skill:python"])
    assert constraints["applied"] == []
    assert constraints["refused"][0]["reason"] == REASON_UNSUPPORTED


def test_an_emphasize_the_kg_supports_is_applied():
    pref = _pref(polarity="emphasize", target_key="proj:diginetica", target_term="diginetica")
    constraints = compile_constraints([pref], _jd(), ["proj:diginetica"])
    assert [a["preference_id"] for a in constraints["applied"]] == ["p1"]
    assert constraints["refused"] == []


def test_refusal_beats_a_hard_preference():
    """The fabrication boundary does not participate in arbitration at all, so
    strength 5 does not buy its way past it."""
    pref = _pref(polarity="emphasize", target_key=None, target_term="quantum computing",
                 strength=5)
    constraints = compile_constraints([pref], _jd(), [])
    assert constraints["applied"] == []
    assert constraints["refused"][0]["reason"] == REASON_UNSUPPORTED


# ── 2/3/4. the strength-vs-criticality table ─────────────────────────────────

@pytest.mark.parametrize("strength,criticality,winner", [
    (5, 5, "preference"),   # rule 2: a hard preference wins even at max criticality
    (5, 1, "preference"),
    (4, 3, "preference"),   # rule 3: strictly firmer than the requirement is central
    (3, 3, "jd"),           # rule 4: ties resolve toward the JD
    (2, 4, "jd"),
    (1, 5, "jd"),
])
def test_the_precedence_table(strength, criticality, winner):
    constraints = compile_constraints(
        [_pref(strength=strength)], _jd(criticality=criticality), [])
    assert constraints["conflicts"][0]["winner"] == winner
    assert bool(constraints["applied"]) is (winner == "preference")


def test_a_losing_preference_is_still_reported():
    """The whole point of the conflict surface: an overridden preference is
    visible, not silently dropped. #121's failure mode was a mis-parse with no
    visible symptom; this is the candidate-side version."""
    constraints = compile_constraints([_pref(strength=1)], _jd(criticality=5), [])
    assert constraints["applied"] == []
    conflict = constraints["conflicts"][0]
    assert conflict["requirement_text"] == "The candidate has machine learning experience."
    assert conflict["criticality"] == 5
    assert "Overridden" in conflict["effect"]


def test_a_winning_preference_reports_the_requirement_it_blocked():
    constraints = compile_constraints([_pref(strength=5)], _jd(criticality=5), [])
    assert constraints["applied"][0]["contested_requirement"] == (
        "The candidate has machine learning experience.")
    assert "Honored" in constraints["conflicts"][0]["effect"]


def test_an_uncontested_preference_produces_no_conflict():
    constraints = compile_constraints(
        [_pref(target_term="fantasy football", target_key="proj:fantasy football")],
        _jd(), [])
    assert len(constraints["applied"]) == 1
    assert constraints["conflicts"] == []


# ── which polarities are arbitrated ──────────────────────────────────────────

def test_emphasize_and_reframe_do_not_contest_a_requirement():
    """Neither removes a requirement's evidence from the resume, so there is
    nothing for the JD to contest — only suppression can cost the match."""
    prefs = [
        _pref(preference_id="p1", polarity="emphasize", target_key="skill:machine learning"),
        _pref(preference_id="p2", polarity="reframe"),
    ]
    constraints = compile_constraints(prefs, _jd(criticality=5), ["skill:machine learning"])
    assert constraints["conflicts"] == []
    assert len(constraints["applied"]) == 2


def test_an_incidental_requirement_cannot_contest_a_preference():
    """`incidental` is by definition not a qualification, so suppressing
    something it mentions costs the candidate nothing."""
    constraints = compile_constraints(
        [_pref(strength=1)], _jd(criticality=5, rtype="incidental"), [])
    assert constraints["conflicts"] == []
    assert len(constraints["applied"]) == 1


# ── matching ─────────────────────────────────────────────────────────────────

def test_a_multiword_requirement_term_matches_inside_the_subject():
    pref = _pref(target_term="deep machine learning work")
    assert len(matching_requirements(pref, _jd())) == 1


def test_matching_is_token_level_not_substring():
    """'ml' must not match 'html'. Substring matching over a resume's worth of
    terms produces exactly this class of wrong suppression."""
    pref = _pref(target_term="html", target_key=None)
    assert matching_requirements(pref, _jd(terms=("ml",))) == []


def test_generic_subject_tokens_do_not_match():
    pref = _pref(target_term="the role", target_key=None)
    assert matching_requirements(pref, _jd(terms=("role",))) == []


# ── determinism ──────────────────────────────────────────────────────────────

def test_the_constraint_set_is_identical_across_runs_and_input_order():
    """Acceptance criterion 3, directly. Row order out of the database is not
    guaranteed, so a constraint set that depended on it would be a real source
    of run-to-run drift."""
    prefs = [
        _pref(preference_id="p2", target_term="kubernetes", target_key="skill:kubernetes"),
        _pref(preference_id="p1"),
        _pref(preference_id="p3", target_term="rust", target_key="skill:rust"),
    ]
    first = compile_constraints(prefs, _jd(), [])
    second = compile_constraints(list(reversed(prefs)), _jd(), [])
    assert first == second
    # p1 contests the JD at equal criticality and loses the tie; the other two
    # are uncontested. Both lists come back in preference_id order regardless of
    # the order the rows arrived in.
    assert [a["preference_id"] for a in first["applied"]] == ["p2", "p3"]
    assert [c["preference_id"] for c in first["conflicts"]] == ["p1"]


# ── rendering ────────────────────────────────────────────────────────────────

def test_render_groups_by_polarity_and_names_the_subject():
    prefs = [
        _pref(preference_id="p1"),
        _pref(preference_id="p2", polarity="emphasize",
              target_key="proj:diginetica", target_term="diginetica",
              text="Lead with the diginetica project."),
    ]
    rendered = render_constraints(
        compile_constraints(prefs, None, ["proj:diginetica"]))
    assert "LEAVE OUT [machine learning]" in rendered
    assert "LEAD WITH [diginetica]" in rendered
    assert rendered.index("LEAVE OUT") < rendered.index("LEAD WITH")
