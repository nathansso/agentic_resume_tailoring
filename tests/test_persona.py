"""Persona semantic tier — traits, the compiled persona, and its wiring (issue #133).

The layer above #129's leaves: a deterministic 1-level index that preserves every
one of them, plus a persisted codified persona. Leaf extraction and supersession
live in `test_preferences.py`; JD arbitration lives in `test_arbitration.py`; the
planner gate lives in `test_preference_gate.py`. What is asserted here is what
this tier adds on top of all three.
"""
import random
from uuid import UUID, uuid4

from sqlmodel import Session, select

import services
from agents.arbitration import compile_constraints, render_constraints
from agents.persona import (
    PERSONA_VERSION, TRAIT_INACTIVE, build_traits, compile_persona, group_key,
    leaf_digest, parse_group_key, persona_digest, persona_features,
    persona_in_scope, superseded_links, trait_label, traits_for,
)
from agents.preferences import (
    STATUS_ACTIVE, STATUS_RETRACTED, STATUS_SUPERSEDED,
)
from agents.tailor_planner import apply_constraints, decision_log_entry
from database.models import Persona, PersonaTrait, User


def _leaf(**kwargs):
    base = {
        "preference_id": str(uuid4()),
        "text": "Do not lead with the Recipe App, it was coursework.",
        "polarity": "suppress",
        "target_type": "project",
        "target_key": "proj:recipe app",
        "target_term": "Recipe App",
        "scope_type": "global",
        "scope_value": None,
        "strength": 3,
        "status": STATUS_ACTIVE,
        "updated_at": "2026-08-02T00:00:00",
    }
    base.update(kwargs)
    return base


def _user(engine) -> User:
    with Session(engine) as session:
        user = User(name="Persona", email=f"persona-{uuid4()}@example.com")
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def _save(user_id, **kwargs):
    """Persist one preference through the real (and only) write path."""
    leaf = _leaf(**kwargs)
    services.apply_preference_decision(user_id, {
        k: v for k, v in leaf.items()
        if k not in ("preference_id", "status", "updated_at")
    })


# ── grouping ─────────────────────────────────────────────────────────────────

def test_the_group_key_is_a_function_of_typed_fields_only():
    a = _leaf(preference_id="a", text="one wording", target_key="proj:x")
    b = _leaf(preference_id="b", text="a completely different wording",
              target_key="proj:y")
    assert group_key(a) == group_key(b) == "suppress|project|global"


def test_role_family_carries_its_value_into_the_key():
    """Two families are two dispositions, so they must not collapse together."""
    ml = _leaf(scope_type="role_family", scope_value="machine_learning")
    research = _leaf(scope_type="role_family", scope_value="research")
    assert group_key(ml) != group_key(research)
    assert group_key(ml).endswith(":machine_learning")


def test_job_scope_does_not_silo_a_trait_per_job():
    """The design decision that makes the tier non-vacuous: keying on the job id
    would mint one trait per application, and a grouping whose every trait has
    exactly one leaf indexes nothing."""
    j1 = _leaf(scope_type="job", scope_value="job-1")
    j2 = _leaf(scope_type="job", scope_value="job-2")
    assert group_key(j1) == group_key(j2)
    assert len(build_traits([j1, j2])) == 1


def test_an_unknown_enum_value_cannot_invent_a_bucket():
    """`group_key` is total. A hand-edited or future-versioned row must fall into
    an existing trait rather than fragmenting the index and breaking the
    exactly-one-trait invariant."""
    assert group_key(_leaf(polarity="banana")) == "suppress|project|global"
    assert group_key(_leaf(target_type="nonsense")) == "suppress|topic|global"


def test_the_key_round_trips_through_its_parts():
    for leaf in (
        _leaf(),
        _leaf(polarity="emphasize", target_type="experience", scope_type="job"),
        _leaf(scope_type="role_family", scope_value="data_science"),
    ):
        parts = parse_group_key(group_key(leaf))
        assert parts["polarity"] == leaf["polarity"]
        assert parts["target_type"] == leaf["target_type"]
        assert parts["scope_type"] == leaf["scope_type"]


def test_the_label_is_a_pure_function_of_the_key():
    key = group_key(_leaf(scope_type="role_family", scope_value="machine_learning"))
    assert trait_label(key) == trait_label(key)
    assert trait_label(key) == "Downplays projects when targeting machine-learning roles"
    assert trait_label("emphasize|experience|global") == (
        "Leads with work experience on every resume")


# ── losslessness ─────────────────────────────────────────────────────────────

def test_every_active_leaf_is_reachable_from_exactly_one_trait():
    """The headline invariant. Asserted as a partition, not as a spot check: a
    grouping bug that dropped or duplicated a leaf cannot pass this."""
    leaves = [
        _leaf(preference_id="a"),
        _leaf(preference_id="b", polarity="emphasize"),
        _leaf(preference_id="c", scope_type="role_family", scope_value="research"),
        _leaf(preference_id="d", target_type="skill", target_key="skill:ml"),
        _leaf(preference_id="e"),
    ]
    traits = build_traits(leaves)
    seen = [lid for t in traits for lid in t["leaf_ids"]]
    assert sorted(seen) == ["a", "b", "c", "d", "e"]
    assert len(seen) == len(set(seen))


def test_a_trait_holds_ids_and_never_content():
    """Lossless index, not lossy summary: there is no field on a trait that could
    disagree with the leaf it points at, because it copies nothing up."""
    trait = build_traits([_leaf(preference_id="a", text="something specific")])[0]
    assert trait["leaf_ids"] == ["a"]
    assert "something specific" not in str(trait)


def test_inactive_leaves_never_enter_a_trait():
    for status in (STATUS_SUPERSEDED, STATUS_RETRACTED):
        assert build_traits([_leaf(status=status)]) == []


# ── determinism ──────────────────────────────────────────────────────────────

def test_two_compiles_of_the_same_leaves_are_byte_identical():
    """Criterion 5, as one equality on the digest. Shuffling between compiles is
    what makes it a real assertion rather than a tautology about list order."""
    leaves = [
        _leaf(preference_id=f"p{i}", polarity=p, target_type=t)
        for i, (p, t) in enumerate(
            [("suppress", "project"), ("emphasize", "experience"),
             ("reframe", "skill"), ("suppress", "topic")])
    ]
    first = compile_persona(list(leaves))
    for seed in range(5):
        shuffled = list(leaves)
        random.Random(seed).shuffle(shuffled)
        assert persona_digest(compile_persona(shuffled)) == persona_digest(first)


def test_the_compile_is_pure_of_clock_and_version_stamped():
    compiled = compile_persona([_leaf()])
    assert compiled["version"] == PERSONA_VERSION
    assert persona_digest(compiled) == persona_digest(compile_persona([_leaf(
        preference_id=compiled["constraints"][0]["preference_id"])]))


def test_the_compiled_constraints_are_the_shape_arbitration_consumes():
    """This tier feeds #129's compile rather than opening a second path, so the
    entries have to be directly consumable by it."""
    leaf = _leaf(preference_id="p1")
    compiled = compile_persona([leaf])
    result = compile_constraints(compiled["constraints"], {}, [])
    assert [a["preference_id"] for a in result["applied"]] == ["p1"]
    assert result["applied"][0]["trait_key"] == "suppress|project|global"


def test_leaf_digest_moves_when_a_leaf_is_edited_without_leaving_active():
    """The staleness check has to cover edits, not just membership: correcting a
    strength changes what the persona says while the active set is identical."""
    before = leaf_digest([_leaf(preference_id="a", updated_at="t1")])
    after = leaf_digest([_leaf(preference_id="a", updated_at="t2")])
    assert before != after


# ── supersession ─────────────────────────────────────────────────────────────

def test_superseded_links_are_kept_out_of_the_compiled_persona():
    """The compiled persona is a function of the *active* leaves only, so
    retracting something does not churn the digest when the effective constraint
    set is unchanged."""
    active = _leaf(preference_id="a")
    gone = _leaf(preference_id="b", status=STATUS_RETRACTED)
    assert persona_digest(compile_persona([active, gone])) == persona_digest(
        compile_persona([active]))
    assert superseded_links([active, gone]) == {"suppress|project|global": ["b"]}


# ── scope ────────────────────────────────────────────────────────────────────

def test_scope_filtering_reuses_the_leaf_rule():
    compiled = compile_persona([
        _leaf(preference_id="g", scope_type="global"),
        _leaf(preference_id="j", scope_type="job", scope_value="job-1"),
        _leaf(preference_id="r", scope_type="role_family",
              scope_value="research"),
    ])
    ids = [c["preference_id"] for c in persona_in_scope(compiled, job_id="job-1")]
    assert ids == ["g", "j"]
    ids = [c["preference_id"] for c in
           persona_in_scope(compiled, job_id="job-2", role_family="research")]
    assert ids == ["g", "r"]


def test_a_trait_out_of_scope_is_not_named_in_the_prompt():
    compiled = compile_persona([
        _leaf(preference_id="g", scope_type="global"),
        _leaf(preference_id="j", polarity="emphasize", scope_type="job",
              scope_value="job-9"),
    ])
    scoped = persona_in_scope(compiled, job_id="job-1")
    keys = [t["group_key"] for t in traits_for(compiled, scoped)]
    assert keys == ["suppress|project|global"]


# ── prompt rendering ─────────────────────────────────────────────────────────

def _applied(*leaves):
    return compile_constraints(
        compile_persona(list(leaves))["constraints"], {}, [])


def test_rendering_without_traits_is_unchanged():
    """Pre-#133 callers must render byte-identically, which is what keeps this
    module independently testable and every existing caller correct."""
    constraints = _applied(_leaf(preference_id="a"))
    assert render_constraints(constraints, None) == render_constraints(constraints)
    assert render_constraints(constraints).startswith("- LEAVE OUT [Recipe App]:")


def test_the_prompt_groups_constraints_under_their_trait_label():
    leaves = [
        _leaf(preference_id="a", target_key="proj:recipe app",
              target_term="Recipe App"),
        _leaf(preference_id="b", target_key="proj:todo cli",
              target_term="Todo CLI", text="Skip the Todo CLI, coursework."),
    ]
    compiled = compile_persona(leaves)
    constraints = compile_constraints(compiled["constraints"], {}, [])
    rendered = render_constraints(constraints, compiled["traits"])
    lines = rendered.splitlines()
    assert lines[0] == "Downplays projects on every resume:"
    assert lines[1].startswith("- LEAVE OUT [Recipe App]")
    assert lines[2].startswith("- LEAVE OUT [Todo CLI]")


def test_a_constraint_with_no_trait_link_still_reaches_the_prompt():
    """A constraint silently dropped because its trait link went missing is the
    failure mode this tier is meant to remove, not introduce."""
    constraints = _applied(_leaf(preference_id="a"))
    rendered = render_constraints(constraints, [
        {"group_key": "other", "label": "Unrelated", "leaf_ids": ["zzz"]},
    ])
    assert "LEAVE OUT [Recipe App]" in rendered


def test_an_empty_constraint_set_renders_empty_with_or_without_traits():
    empty = {"applied": [], "conflicts": [], "refused": []}
    assert render_constraints(empty) == ""
    assert render_constraints(empty, [{"group_key": "k", "label": "L",
                                       "leaf_ids": ["a"]}]) == ""


# ── #119 per-item context features ───────────────────────────────────────────

def test_persona_features_bind_to_the_item_they_target():
    constraints = compile_persona([
        _leaf(preference_id="a", target_key="proj:recipe app"),
        _leaf(preference_id="b", polarity="emphasize",
              target_key="exp:barista|coffee co", target_type="experience"),
    ])["constraints"]

    hit = persona_features(constraints, "proj:recipe app")
    assert hit["has_active_suppress_target"] is True
    assert hit["has_active_emphasize_target"] is False
    assert hit["trait_keys"] == ["suppress|project|global"]

    miss = persona_features(constraints, "proj:untouched")
    assert miss["has_active_suppress_target"] is False
    assert miss["trait_keys"] == []


def test_persona_features_are_stamped_on_every_action_not_only_gated_ones():
    """"No preference bound here" is exactly as informative to a learner as the
    positive case; a feature present only on hits cannot be conditioned on."""
    items = [
        {"key": "proj:recipe app", "section": "projects", "label": "Recipe App"},
        {"key": "proj:other", "section": "projects", "label": "Other"},
    ]
    actions = [
        {"item_key": i["key"], "section": i["section"], "op": "revise",
         "strategy": "tighten"} for i in items
    ]
    constraints = _applied(_leaf(preference_id="a"))
    actions, _ = apply_constraints(actions, constraints, items)
    assert all("persona" in a for a in actions)
    by_key = {a["item_key"]: a for a in actions}
    assert by_key["proj:recipe app"]["persona"]["has_active_suppress_target"] is True
    assert by_key["proj:other"]["persona"]["has_active_suppress_target"] is False


def test_persona_features_round_trip_through_the_decision_log():
    """Criterion 7: the features have to survive into `tailoring_decisions`,
    which is the only artifact #119's learner ever reads."""
    items = [{"key": "proj:recipe app", "section": "projects",
              "label": "Recipe App"}]
    actions = [{"item_key": "proj:recipe app", "section": "projects",
                "op": "revise", "strategy": "tighten"}]
    constraints = _applied(_leaf(preference_id="a"))
    actions, enforcement = apply_constraints(actions, constraints, items)
    plan = {"actions": actions, "knobs": {}, "planner": "llm",
            "constraint_enforcement": enforcement}
    entry = decision_log_entry(plan, {"attempts": 1}, {}, constraints=constraints)
    logged = entry["actions"][0]["persona"]
    assert logged["has_active_suppress_target"] is True
    assert logged["trait_keys"] == ["suppress|project|global"]
    assert entry["constraints"]["applied"][0]["trait_key"] == "suppress|project|global"


def test_an_empty_constraint_set_stamps_no_persona_block():
    """Criterion 10 at the action level: a user with no preferences produces the
    byte-for-byte pre-#129 action dict."""
    actions = [{"item_key": "proj:x", "section": "projects", "op": "revise"}]
    out, enforcement = apply_constraints(
        actions, {"applied": [], "conflicts": [], "refused": []}, [])
    assert "persona" not in out[0]
    assert enforcement == {}


# ── persistence ──────────────────────────────────────────────────────────────

def test_the_persona_persists_and_survives_a_reload(isolated_engine):
    user = _user(isolated_engine)
    _save(user.user_id)

    with Session(isolated_engine) as session:
        row = session.exec(
            select(Persona).where(Persona.user_id == user.user_id)).first()
        assert row is not None
        assert row.compile_version == PERSONA_VERSION
        assert row.compiled_hash

    traits = services.load_persona_traits(user.user_id)
    assert [t["group_key"] for t in traits] == ["suppress|project|global"]
    assert traits[0]["label"] == "Downplays projects on every resume"


def test_the_persisted_index_is_lossless(isolated_engine):
    """Criterion 3 end to end: every active leaf on the table is reachable from
    exactly one active trait."""
    user = _user(isolated_engine)
    _save(user.user_id, target_key="proj:recipe app", target_term="Recipe App")
    _save(user.user_id, target_key="proj:todo cli", target_term="Todo CLI",
          text="Skip the Todo CLI.")
    _save(user.user_id, polarity="emphasize", target_type="experience",
          target_key="exp:barista|coffee co", target_term="Barista",
          text="Lead with the barista job.")

    active = {p["preference_id"] for p in services.load_preferences(user.user_id)}
    reached = [
        lid for t in services.load_persona_traits(user.user_id)
        for lid in t["leaf_ids"]
    ]
    assert sorted(reached) == sorted(active)
    assert len(reached) == len(set(reached))


def test_trait_ids_are_stable_across_a_recompute(isolated_engine):
    """Criterion 2, and the reason grouping is deterministic rather than
    emergent: trait membership is a #119 RL context bucket, so a churning id
    would make the policy's context non-stationary."""
    user = _user(isolated_engine)
    _save(user.user_id, target_key="proj:recipe app")
    before = {t["group_key"]: t["trait_id"]
              for t in services.load_persona_traits(user.user_id)}

    # A second leaf in the same trait, then two bare recompiles.
    _save(user.user_id, target_key="proj:todo cli", text="Skip the Todo CLI.")
    services.rebuild_persona(user.user_id)
    services.rebuild_persona(user.user_id)

    after = {t["group_key"]: t["trait_id"]
             for t in services.load_persona_traits(user.user_id)}
    assert after["suppress|project|global"] == before["suppress|project|global"]


def test_an_unchanged_rebuild_does_not_touch_the_row(isolated_engine):
    user = _user(isolated_engine)
    _save(user.user_id)
    with Session(isolated_engine) as session:
        before = session.exec(
            select(Persona).where(Persona.user_id == user.user_id)).first().updated_at
    services.rebuild_persona(user.user_id)
    with Session(isolated_engine) as session:
        after = session.exec(
            select(Persona).where(Persona.user_id == user.user_id)).first().updated_at
    assert after == before


def test_a_retracted_leaf_keeps_its_trait_link(isolated_engine):
    """Criterion 4's genuinely-new half. #129 keeps the superseded *row*; this
    keeps the *link*, so a trait records the whole trajectory of a disposition
    rather than only its current state."""
    user = _user(isolated_engine)
    _save(user.user_id, target_key="proj:recipe app")
    _save(user.user_id, target_key="proj:todo cli", text="Skip the Todo CLI.")
    victim = services.load_preferences(user.user_id)[0]["preference_id"]

    services.retract_preference(user.user_id, UUID(victim))

    trait = services.load_persona_traits(user.user_id)[0]
    assert victim not in trait["leaf_ids"]
    assert victim in trait["superseded_leaf_ids"]
    # And the leaf row itself is still there — negation must not expire.
    kept = services.load_preferences(user.user_id, include_inactive=True)
    assert victim in {p["preference_id"] for p in kept}


def test_a_trait_that_loses_every_leaf_goes_inactive_rather_than_away(isolated_engine):
    user = _user(isolated_engine)
    _save(user.user_id)
    only = services.load_preferences(user.user_id)[0]["preference_id"]

    services.retract_preference(user.user_id, UUID(only))

    assert services.load_persona_traits(user.user_id) == []
    kept = services.load_persona_traits(user.user_id, include_inactive=True)
    assert len(kept) == 1
    assert kept[0]["status"] == TRAIT_INACTIVE
    assert kept[0]["superseded_leaf_ids"] == [only]


def test_an_edited_leaf_moves_between_traits(isolated_engine):
    user = _user(isolated_engine)
    _save(user.user_id)
    pid = services.load_preferences(user.user_id)[0]["preference_id"]

    services.update_preference(user.user_id, UUID(pid), {"polarity": "emphasize"})

    traits = services.load_persona_traits(user.user_id)
    assert [t["group_key"] for t in traits] == ["emphasize|project|global"]
    assert traits[0]["leaf_ids"] == [pid]


# ── inspect and correct (criterion 9) ────────────────────────────────────────

def test_a_corrected_trait_label_survives_a_recompile(isolated_engine):
    """Same contract `UserPreference.edited` carries: a human correction is never
    silently overwritten by a later recompile."""
    user = _user(isolated_engine)
    _save(user.user_id)
    trait_id = services.load_persona_traits(user.user_id)[0]["trait_id"]

    updated = services.update_persona_trait(
        user.user_id, UUID(trait_id), "Plays down student projects")
    assert updated["label"] == "Plays down student projects"
    assert updated["edited"] is True

    _save(user.user_id, target_key="proj:todo cli", text="Skip the Todo CLI.")
    assert services.load_persona_traits(user.user_id)[0]["label"] == (
        "Plays down student projects")


def test_a_corrected_label_is_what_the_planner_reads(isolated_engine):
    """Criterion 9 has to bite where the label is actually used, and there is
    exactly one such place: `render_constraints` heads the prompt block with it.
    A correction that stopped at the inspect surface would leave the planner
    reading the template phrasing the user had just rejected — the edit surface
    would be decorative."""
    user = _user(isolated_engine)
    _save(user.user_id)
    trait_id = services.load_persona_traits(user.user_id)[0]["trait_id"]
    services.update_persona_trait(
        user.user_id, UUID(trait_id), "Plays down student coursework")

    persona = services.get_active_persona(user.user_id, job_id="job-1")
    assert [t["label"] for t in persona["traits"]] == ["Plays down student coursework"]

    constraints = compile_constraints(
        persona["constraints"], {}, ["proj:recipe app"])
    assert render_constraints(constraints, persona["traits"]).startswith(
        "Plays down student coursework:")


def test_a_corrected_label_does_not_leak_into_the_compiled_persona(isolated_engine):
    """The other half: criterion 5 says `compiled` is a pure function of the
    active leaves, so the correction is overlaid at read time and never folded
    into the artifact. Two users who said the same things still compile to the
    same bytes, whatever either of them renamed."""
    user = _user(isolated_engine)
    _save(user.user_id)
    with Session(isolated_engine) as session:
        before = session.exec(
            select(Persona).where(Persona.user_id == user.user_id)).first().compiled_hash

    trait_id = services.load_persona_traits(user.user_id)[0]["trait_id"]
    services.update_persona_trait(user.user_id, UUID(trait_id), "Renamed")
    services.rebuild_persona(user.user_id)

    with Session(isolated_engine) as session:
        row = session.exec(
            select(Persona).where(Persona.user_id == user.user_id)).first()
        assert row.compiled_hash == before
        assert [t["label"] for t in row.compiled["traits"]] == [
            "Downplays projects on every resume"]


def test_a_trait_belonging_to_another_user_cannot_be_edited(isolated_engine):
    """Issue #73: every id-bearing path re-checks ownership."""
    victim = _user(isolated_engine)
    attacker = _user(isolated_engine)
    _save(victim.user_id)
    trait_id = services.load_persona_traits(victim.user_id)[0]["trait_id"]

    assert services.update_persona_trait(
        attacker.user_id, UUID(trait_id), "hijacked") is None
    assert services.load_persona_traits(victim.user_id)[0]["label"] == (
        "Downplays projects on every resume")


def test_traits_are_scoped_to_their_owner(isolated_engine):
    a, b = _user(isolated_engine), _user(isolated_engine)
    _save(a.user_id)
    assert services.load_persona_traits(b.user_id) == []
    assert services.get_active_persona(b.user_id)["constraints"] == []


# ── the read path ────────────────────────────────────────────────────────────

def test_get_active_persona_returns_the_constraints_arbitration_expects(isolated_engine):
    user = _user(isolated_engine)
    _save(user.user_id)
    persona = services.get_active_persona(user.user_id, job_id="job-1")
    assert len(persona["constraints"]) == 1
    assert persona["constraints"][0]["trait_key"] == "suppress|project|global"
    assert [t["group_key"] for t in persona["traits"]] == ["suppress|project|global"]


def test_a_stale_stored_persona_is_ignored_rather_than_served(isolated_engine):
    """The stored persona is a cache; the leaves are the authority. A missed
    rebuild must cost inspectability, never a preference."""
    user = _user(isolated_engine)
    _save(user.user_id)
    # Corrupt the stored artifact the way a missed rebuild would.
    with Session(isolated_engine) as session:
        row = session.exec(
            select(Persona).where(Persona.user_id == user.user_id)).first()
        row.compiled = {"version": PERSONA_VERSION, "traits": [], "constraints": []}
        row.leaf_digest = "stale"
        session.add(row)
        session.commit()

    persona = services.get_active_persona(user.user_id, job_id="job-1")
    assert len(persona["constraints"]) == 1


def test_an_absent_persona_yields_empty_and_writes_nothing(isolated_engine):
    user = _user(isolated_engine)
    persona = services.get_active_persona(user.user_id, job_id="job-1")
    assert persona == {"traits": [], "constraints": []}
    with Session(isolated_engine) as session:
        assert session.exec(
            select(Persona).where(Persona.user_id == user.user_id)).first() is None


def test_the_read_path_never_writes(isolated_engine):
    """The write barrier, in its strongest available form. #118 established that
    a pipeline able to write the user's tier launders its own output into a
    counterfeit user choice; for an *inferred* tier that means suppressing an
    item, observing the suppression, and citing it back as the user's own
    instruction. `get_active_persona` is the only thing tailoring calls."""
    user = _user(isolated_engine)
    _save(user.user_id)
    with Session(isolated_engine) as session:
        before = session.exec(
            select(Persona).where(Persona.user_id == user.user_id)).first()
        stamp = (before.compiled_hash, before.updated_at)
        trait_stamp = [
            (t.trait_id, t.updated_at) for t in session.exec(
                select(PersonaTrait).where(PersonaTrait.user_id == user.user_id)).all()
        ]

    for _ in range(3):
        services.get_active_persona(user.user_id, job_id="job-1")

    with Session(isolated_engine) as session:
        after = session.exec(
            select(Persona).where(Persona.user_id == user.user_id)).first()
        assert (after.compiled_hash, after.updated_at) == stamp
        assert [
            (t.trait_id, t.updated_at) for t in session.exec(
                select(PersonaTrait).where(PersonaTrait.user_id == user.user_id)).all()
        ] == trait_stamp


# ── inherited #129 guarantees, retested through the persona path ─────────────

def test_a_global_suppression_still_binds_on_every_job(isolated_engine):
    """Criterion 6 is inherited from #129 — retested here so the trait tier
    cannot have broken the negation-durability guarantee."""
    user = _user(isolated_engine)
    _save(user.user_id, scope_type="global")
    for job in ("job-1", "job-2", "job-3"):
        constraints = compile_constraints(
            services.get_active_persona(user.user_id, job_id=job)["constraints"],
            {}, [])
        assert [a["target_key"] for a in constraints["applied"]] == ["proj:recipe app"]


def test_an_unsupported_emphasize_is_still_refused_through_the_persona_path(
        isolated_engine):
    """Criterion 8 is inherited: the faithfulness refusal sits above this tier
    and nothing here may relax it."""
    user = _user(isolated_engine)
    _save(user.user_id, polarity="emphasize", target_type="topic",
          target_key=None, target_term="leadership",
          text="Lead with my leadership.")
    constraints = compile_constraints(
        services.get_active_persona(user.user_id, job_id="job-1")["constraints"],
        {}, ["proj:recipe app"])
    assert constraints["applied"] == []
    assert constraints["refused"][0]["reason"] == "unsupported_by_knowledge_graph"
