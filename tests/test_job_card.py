"""JobCard compile / classify / selection tests (issue #137).

Covers the pure projection layer in `agents/job_card.py`: that the compile is
deterministic, that the negation signal (user-rejected items) round-trips and is
never dropped, that the cached `role_family` classify goes through the #142
`get_extractor` seam, and that selection is stable and correctly ordered.

Offline by construction — the only LLM call in the whole module is the
`role_family` classify, and it is scripted here with the same `_Scripted`
stand-in `tests/test_knowledge_extraction.py` uses.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import agents.job_card as jc
from agents.extraction_schemas import RoleFamily, RoleFamilyClassification


class _Scripted:
    """A StructuredExtractor stand-in returning a fixed validated model."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        return self.payload


class _Failing:
    def invoke(self, _messages):
        raise RuntimeError("provider is down")


def _decision(
    *actions, planner="llm", revision_notes="", user_score=None, delta=10.0
):
    reward = {"delta": delta}
    if user_score is not None:
        reward["user_score"] = user_score
    return {
        "planner": planner,
        "revision_notes": revision_notes,
        "actions": list(actions),
        "reward": reward,
    }


def _action(item_key, op, label=None, section="project", rationale=""):
    return {
        "item_key": item_key, "op": op, "label": label or item_key,
        "section": section, "rationale": rationale,
    }


def _result(**overrides):
    base = dict(
        ats_score=61.0,
        tailored_score_breakdown={
            "composite": 78.5, "baseline_composite": 56.0, "delta": 22.5,
            "skill_coverage": {"score": 90.0}, "keyword_coverage": {"score": 70.0},
        },
        score_breakdown={},
        matched_skills={
            "python": {"match_type": "direct"},
            "_explainability": {"emphasized": ["pandas (≈numpy)"]},
        },
        tailored_resume_content={
            "experiences": [
                {"title": "ML Engineer", "company": "Nimbus"},
                {"title": "Backend Engineer", "company": "Bluefin"},
            ],
            "projects": [{"name": "SemanticSearch"}, {"name": "StreamBoard"}],
            "skills_emphasized": ["Python", "PyTorch"],
        },
        tailoring_decisions=[],
        verification_status="pending",
        created_at=datetime(2026, 7, 1),
        updated_at=datetime(2026, 7, 20),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _job(**overrides):
    base = dict(title="ML Engineer", company="Acme", status="tailored",
                description="Build and ship ML systems")
    base.update(overrides)
    return SimpleNamespace(**base)


# ── deterministic compile ─────────────────────────────────────────────────────

def test_compile_is_deterministic():
    """Two compiles of the same result produce identical cards — the property
    that makes the card a projection rather than a summary."""
    job, result = _job(), _result()
    first = jc.compile_card_payload(job, result, role_family="machine_learning")
    second = jc.compile_card_payload(job, result, role_family="machine_learning")
    assert first == second
    assert jc.payload_digest(first) == jc.payload_digest(second)


def test_compile_carries_the_sufficient_statistics():
    result = _result(tailoring_decisions=[
        _decision(_action("proj:streamboard", "keep"), user_score=4),
    ])
    payload = jc.compile_card_payload(_job(), result, role_family="machine_learning")

    assert payload["ats"]["composite"] == 78.5
    assert payload["ats"]["delta"] == 22.5
    assert payload["ats"]["components"]["skill_coverage"] == 90.0
    assert payload["emphasized"]["led_experience"] == "ML Engineer @ Nimbus"
    assert payload["emphasized"]["led_project"] == "SemanticSearch"
    assert "Python" in payload["emphasized"]["skills"]
    # _explainability lives inside the matched_skills dict, not beside it
    assert "pandas (≈numpy)" in payload["emphasized"]["skills"]
    assert payload["user_score"] == 4
    assert payload["job"]["terminal_status"] == "tailored"
    assert payload["job"]["verification_status"] == "pending"


def test_compile_falls_back_to_baseline_breakdown_then_bare_score():
    """A result that never got a tailored breakdown still compiles."""
    payload = jc.compile_card_payload(
        _job(),
        _result(tailored_score_breakdown={}, score_breakdown={"composite": 44.0}),
    )
    assert payload["ats"]["composite"] == 44.0

    payload = jc.compile_card_payload(
        _job(), _result(tailored_score_breakdown={}, score_breakdown={}))
    assert payload["ats"]["composite"] == 61.0  # the bare ats_score


def test_compile_survives_json_columns_round_tripping_as_strings():
    """SQLite can hand a JSON column back as text; a card must not come out empty."""
    payload = jc.compile_card_payload(_job(), _result(
        tailored_resume_content='{"experiences": [{"title": "SRE", "company": "Acme"}]}',
        tailored_score_breakdown='{"composite": 51.0}',
        tailoring_decisions="[]",
    ))
    assert payload["emphasized"]["led_experience"] == "SRE @ Acme"
    assert payload["ats"]["composite"] == 51.0


def test_compile_ignores_an_errored_tailoring_run():
    payload = jc.compile_card_payload(
        _job(), _result(tailored_resume_content={"error": "generation failed"}))
    assert payload["emphasized"]["experiences"] == []
    assert payload["emphasized"]["led_experience"] is None


# ── the negation signal ───────────────────────────────────────────────────────

def test_user_rejected_item_round_trips_into_the_card():
    """The negation guard: what the user took *out* is the field a generic
    recap loses, so it must survive the compile intact."""
    result = _result(tailoring_decisions=[
        _decision(
            _action("proj:pixel adventure", "delete", "Pixel Adventure",
                    rationale="not relevant to backend roles"),
            planner="chat_approved", revision_notes="drop the game project",
            user_score=5,
        ),
    ])
    payload = jc.compile_card_payload(_job(), result, role_family="software_engineering")

    rejected = payload["rejected_items"]
    assert len(rejected) == 1
    assert rejected[0]["label"] == "Pixel Adventure"
    assert rejected[0]["source"] == "user"
    assert rejected[0]["op"] == "delete"

    # …and survives rendering at the tightest possible budget.
    card = {"card_id": "c1", "payload": payload, "index_keys": [],
            "role_family": "software_engineering"}
    rendered = jc.render_cards([card], budget=1)
    assert "Pixel Adventure" in rendered
    assert "User removed" in rendered


def test_planner_and_user_rejections_are_labelled_apart():
    """A planner's own drop is a far weaker preference signal than a human's;
    conflating them would teach the next job a preference nobody stated."""
    result = _result(tailoring_decisions=[
        _decision(_action("proj:a", "delete", "Alpha")),
        _decision(_action("proj:b", "delete", "Beta"),
                  planner="chat_approved", revision_notes="cut Beta"),
    ])
    by_label = {
        r["label"]: r["source"]
        for r in jc.compile_card_payload(_job(), result)["rejected_items"]
    }
    assert by_label == {"Alpha": "planner", "Beta": "user"}


def test_a_reversed_rejection_is_not_a_standing_rejection():
    """Deleted on run 1, kept on run 2 — the user changed their mind, and the
    card must not carry the stale negation forward."""
    result = _result(tailoring_decisions=[
        _decision(_action("proj:a", "delete", "Alpha"),
                  planner="chat_approved", revision_notes="cut Alpha"),
        _decision(_action("proj:a", "keep", "Alpha"),
                  planner="chat_approved", revision_notes="actually keep Alpha"),
    ])
    assert jc.compile_card_payload(_job(), result)["rejected_items"] == []


def test_replace_counts_as_a_rejection_of_the_replaced_item():
    result = _result(tailoring_decisions=[
        _decision(_action("proj:a", "replace", "Alpha"),
                  planner="chat_approved", revision_notes="swap Alpha out"),
    ])
    rejected = jc.compile_card_payload(_job(), result)["rejected_items"]
    assert [(r["label"], r["op"], r["source"]) for r in rejected] == [
        ("Alpha", "replace", "user")]


def test_user_rejections_sort_ahead_of_planner_rejections():
    result = _result(tailoring_decisions=[
        _decision(_action("proj:a", "delete", "Alpha"),
                  _action("proj:b", "delete", "Beta"),
                  planner="chat_approved", revision_notes="cut both"),
        _decision(_action("proj:c", "delete", "Gamma")),
    ])
    sources = [r["source"] for r in
               jc.compile_card_payload(_job(), result)["rejected_items"]]
    assert sources == ["user", "user", "planner"]


def test_rejections_are_not_added_to_the_index_keys():
    """Negation cannot be represented in a similarity index (#129 finding 1), so
    rejections are pushed with the card rather than indexed for retrieval."""
    result = _result(tailoring_decisions=[
        _decision(_action("proj:pixel adventure", "delete", "Pixel Adventure"),
                  planner="chat_approved", revision_notes="drop it"),
    ])
    payload = jc.compile_card_payload(_job(), result, role_family="machine_learning")
    keys = jc.build_index_keys(payload)
    assert keys, "the card should still index its positive facts"
    assert not any("pixel" in k for k in keys)


# ── the one LLM call: role_family ─────────────────────────────────────────────

def test_classify_role_family_goes_through_the_extraction_seam():
    extractor = _Scripted(
        RoleFamilyClassification(role_family=RoleFamily.MACHINE_LEARNING))
    family = jc.classify_role_family(
        "ML Engineer", "Acme", "Train and serve models", extractor=extractor)
    assert family == "machine_learning"
    assert extractor.calls == 1


def test_classify_role_family_degrades_to_other_on_failure():
    """A classification outage must not fail a card compile, which must not
    fail a tailoring run."""
    assert jc.classify_role_family("ML Engineer", extractor=_Failing()) == "other"


def test_classify_role_family_skips_the_call_with_nothing_to_classify():
    extractor = _Scripted(
        RoleFamilyClassification(role_family=RoleFamily.MACHINE_LEARNING))
    assert jc.classify_role_family("", "", "", extractor=extractor) == "other"
    assert extractor.calls == 0


def test_role_family_key_is_stable_and_input_sensitive():
    a = jc.role_family_key("ML Engineer", "Acme", "Train models")
    assert a == jc.role_family_key("ML Engineer", "Acme", "Train models")
    assert a == jc.role_family_key("  ml   engineer ", "ACME!", "train  models")
    assert a != jc.role_family_key("Data Analyst", "Acme", "Train models")


# ── selection ─────────────────────────────────────────────────────────────────

def _card(card_id, *, role_family=None, keys=(), embedding=None, age_days=1):
    return {
        "card_id": card_id,
        "payload": {"job": {"title": card_id}, "role_family": role_family},
        "index_keys": list(keys),
        "role_family": role_family,
        "embedding": embedding,
        "source_updated_at": datetime(2026, 7, 20) - timedelta(days=age_days),
    }


def test_selection_orders_by_relevance_and_is_stable():
    """A fixed JD + card set must give the same count and the same order every
    time — the acceptance criterion for injection."""
    cards = [
        _card("far", role_family="design", keys=["skill:figma"],
              embedding=[0.0, 1.0]),
        _card("near", role_family="machine_learning",
              keys=["skill:python", "role:machine learning"], embedding=[1.0, 0.0]),
        _card("mid", role_family="machine_learning", keys=["skill:python"],
              embedding=[0.7071, 0.7071]),
    ]
    kwargs = dict(jd_vector=[1.0, 0.0], jd_skill_names=["Python"],
                  role_family="machine_learning", now=datetime(2026, 7, 20))

    first = [c["card_id"] for c in jc.select_cards(cards, **kwargs)]
    second = [c["card_id"] for c in jc.select_cards(list(reversed(cards)), **kwargs)]
    assert first == ["near", "mid", "far"]
    assert first == second, "ordering must not depend on input order"

    scores = [c["_selection"]["score"] for c in jc.select_cards(cards, **kwargs)]
    assert scores == sorted(scores, reverse=True)


def test_selection_exercises_the_vector_seam_on_sqlite():
    """Embedding similarity is the deciding signal here — everything else ties —
    so a non-empty, correctly ordered result proves the numpy candidates path
    genuinely ran rather than silently returning nothing."""
    cards = [
        _card("orthogonal", role_family="research", keys=[], embedding=[0.0, 1.0]),
        _card("aligned", role_family="research", keys=[], embedding=[1.0, 0.0]),
    ]
    selected = jc.select_cards(cards, jd_vector=[1.0, 0.0],
                               now=datetime(2026, 7, 20))
    assert [c["card_id"] for c in selected] == ["aligned", "orthogonal"]
    assert selected[0]["_selection"]["embedding_similarity"] == pytest.approx(1.0)
    assert selected[1]["_selection"]["embedding_similarity"] == pytest.approx(0.0)


def test_selection_still_ranks_without_any_embeddings():
    """Embeddings are optional (the model may be unavailable); the other three
    signals must still separate the cards."""
    cards = [
        _card("unrelated", role_family="design", keys=["skill:figma"]),
        _card("related", role_family="machine_learning",
              keys=["skill:python", "role:machine learning"]),
    ]
    selected = jc.select_cards(
        cards, jd_vector=None, jd_skill_names=["Python"],
        role_family="machine_learning", now=datetime(2026, 7, 20))
    assert [c["card_id"] for c in selected] == ["related", "unrelated"]
    assert selected[0]["_selection"]["score"] > selected[1]["_selection"]["score"]


def test_unclassifiable_cards_do_not_match_each_other():
    """'other' is the null label, not a family."""
    selected = jc.select_cards(
        [_card("a", role_family="other", keys=[])],
        role_family="other", now=datetime(2026, 7, 20))
    assert selected[0]["_selection"]["role_match"] == 0.0


def test_recency_breaks_ties_toward_the_most_recent_card():
    cards = [
        _card("old", role_family="research", keys=["skill:python"], age_days=400),
        _card("recent", role_family="research", keys=["skill:python"], age_days=2),
    ]
    selected = jc.select_cards(cards, jd_skill_names=["Python"],
                               role_family="research", now=datetime(2026, 7, 20))
    assert [c["card_id"] for c in selected] == ["recent", "old"]


def test_selection_is_capped_at_top_n():
    cards = [_card(f"c{i}", role_family="research") for i in range(10)]
    assert len(jc.select_cards(cards, now=datetime(2026, 7, 20))) == jc.top_n()
    assert len(jc.select_cards(cards, limit=2, now=datetime(2026, 7, 20))) == 2


def test_selection_of_nothing_is_nothing():
    assert jc.select_cards([]) == []
    assert jc.render_cards([]) == ""


# ── bounded rendering ─────────────────────────────────────────────────────────

def test_render_cost_does_not_grow_with_the_number_of_jobs():
    """Bounded token budget: twenty cards must not cost more than three."""
    payload = jc.compile_card_payload(_job(), _result(), role_family="research")
    many = [
        {"card_id": f"c{i}", "payload": payload, "index_keys": [],
         "role_family": "research"}
        for i in range(20)
    ]
    rendered = jc.render_cards(many, budget=120)
    assert rendered
    assert jc._estimate_tokens(rendered) <= 120 + jc._estimate_tokens(
        jc._render_card(many[0]))


def test_render_always_emits_the_top_card_even_over_budget():
    """An over-long card must not silently switch the whole memory tier off."""
    payload = jc.compile_card_payload(_job(), _result(), role_family="research")
    card = {"card_id": "c1", "payload": payload, "index_keys": [],
            "role_family": "research"}
    assert "ML Engineer" in jc.render_cards([card], budget=1)


# ── persistence + event-driven lifecycle ──────────────────────────────────────

def _seed_job_and_result(engine, user_id, *, title="ML Engineer", company="Acme",
                         status="tailored", description="Build ML systems",
                         content=None, decisions=None, embedding=None):
    """A completed job plus its result, as the tailoring pipeline would leave them."""
    import json as _json

    from sqlmodel import Session

    from database.models import JobDescription, UserJobResult

    with Session(engine) as session:
        job = JobDescription(
            user_id=user_id, title=title, company=company,
            description=description, status=status,
            embedding=_json.dumps(embedding) if embedding else None,
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        result = UserJobResult(
            user_id=user_id, job_id=job.job_id, ats_score=72.0,
            tailored_score_breakdown={"composite": 78.5, "delta": 22.5},
            tailored_resume_content=content if content is not None else {
                "experiences": [{"title": "ML Engineer", "company": "Nimbus"}],
                "projects": [{"name": "SemanticSearch"}],
                "skills_emphasized": ["Python"],
            },
            tailoring_decisions=decisions or [],
        )
        session.add(result)
        session.commit()
        session.refresh(result)
        return job.job_id, result.result_id


@pytest.fixture()
def scripted_family(monkeypatch):
    """Pin the one LLM call and count how often it actually fires."""
    calls = []

    def _classify(title, company="", description="", **kwargs):
        calls.append((title, company, description))
        return "machine_learning"

    monkeypatch.setattr(jc, "classify_role_family", _classify)
    return calls


def test_rebuild_persists_a_card(isolated_engine, scripted_family):
    import services

    from conftest import _seed_user_and_skill
    user = _seed_user_and_skill(isolated_engine)
    job_id, result_id = _seed_job_and_result(isolated_engine, user.user_id)

    card_id = services.rebuild_job_card(user.user_id, job_id)
    assert card_id is not None

    from sqlmodel import Session

    from database.models import JobCard
    with Session(isolated_engine) as session:
        card = session.get(JobCard, card_id)
    assert card.role_family == "machine_learning"
    assert card.result_id == result_id
    assert card.payload["emphasized"]["led_project"] == "SemanticSearch"
    assert card.index_keys and "role:machine learning" in card.index_keys
    assert card.payload_hash


def test_rebuild_is_idempotent_and_reuses_the_cached_role_family(
    isolated_engine, scripted_family,
):
    """A rebuild must not be an LLM call — that is what makes the compile cheap
    enough to run on every result change."""
    import services

    from conftest import _seed_user_and_skill
    user = _seed_user_and_skill(isolated_engine)
    job_id, _ = _seed_job_and_result(isolated_engine, user.user_id)

    first = services.rebuild_job_card(user.user_id, job_id)
    second = services.rebuild_job_card(user.user_id, job_id)

    assert first == second, "a rebuild must update in place, never duplicate"
    assert len(scripted_family) == 1, "role_family should be classified exactly once"

    from sqlmodel import Session, select

    from database.models import JobCard
    with Session(isolated_engine) as session:
        assert len(session.exec(select(JobCard)).all()) == 1


def test_editing_the_job_description_reclassifies(isolated_engine, scripted_family):
    """The cache key covers the classify inputs, so a real JD change invalidates."""
    import services

    from sqlmodel import Session

    from conftest import _seed_user_and_skill
    from database.models import JobDescription

    user = _seed_user_and_skill(isolated_engine)
    job_id, _ = _seed_job_and_result(isolated_engine, user.user_id)
    services.rebuild_job_card(user.user_id, job_id)

    with Session(isolated_engine) as session:
        job = session.get(JobDescription, job_id)
        job.description = "Lead a product design team and own the roadmap"
        session.add(job)
        session.commit()

    services.rebuild_job_card(user.user_id, job_id)
    assert len(scripted_family) == 2


def test_rebuild_skips_a_job_with_no_tailored_content(isolated_engine, scripted_family):
    """Nothing was successfully tailored, so there is no outcome to distil."""
    import services

    from conftest import _seed_user_and_skill
    user = _seed_user_and_skill(isolated_engine)
    job_id, _ = _seed_job_and_result(
        isolated_engine, user.user_id, content={"error": "generation failed"})

    assert services.rebuild_job_card(user.user_id, job_id) is None
    assert scripted_family == [], "a skipped compile must not spend an LLM call"


def test_rebuild_never_raises_on_a_missing_job(isolated_engine):
    """A card is an optimization — losing one must never break its caller."""
    import uuid

    import services

    from conftest import _seed_user_and_skill
    user = _seed_user_and_skill(isolated_engine)
    assert services.rebuild_job_card(user.user_id, uuid.uuid4()) is None


def test_load_job_cards_excludes_the_active_job_and_unfinished_jobs(
    isolated_engine, scripted_family,
):
    import services

    from conftest import _seed_user_and_skill
    user = _seed_user_and_skill(isolated_engine)
    done_id, _ = _seed_job_and_result(isolated_engine, user.user_id, title="Done Role")
    active_id, _ = _seed_job_and_result(isolated_engine, user.user_id, title="Active Role")
    draft_id, _ = _seed_job_and_result(
        isolated_engine, user.user_id, title="Draft Role", status="analyzed")

    for job_id in (done_id, active_id, draft_id):
        services.rebuild_job_card(user.user_id, job_id)

    loaded = services.load_job_cards(user.user_id, exclude_job_id=active_id)
    assert [c["payload"]["job"]["title"] for c in loaded] == ["Done Role"]


def test_load_job_cards_is_scoped_to_the_owner(isolated_engine, scripted_family):
    import services

    from sqlmodel import Session

    from conftest import _seed_user_and_skill
    from database.models import User

    owner = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as session:
        other = User(name="Other", email="other@example.com")
        session.add(other)
        session.commit()
        session.refresh(other)
        other_id = other.user_id

    job_id, _ = _seed_job_and_result(isolated_engine, owner.user_id)
    services.rebuild_job_card(owner.user_id, job_id)

    assert len(services.load_job_cards(owner.user_id)) == 1
    assert services.load_job_cards(other_id) == []


def test_load_job_cards_carries_the_source_job_vector(isolated_engine, scripted_family):
    """Ranking scores a card against its source job's already-cached JD centroid,
    so no new embedding infrastructure is needed."""
    import services

    from conftest import _seed_user_and_skill
    user = _seed_user_and_skill(isolated_engine)
    job_id, _ = _seed_job_and_result(
        isolated_engine, user.user_id, embedding=[1.0, 0.0, 0.0])
    services.rebuild_job_card(user.user_id, job_id)

    loaded = services.load_job_cards(user.user_id)
    assert loaded[0]["embedding"] is not None
    assert list(loaded[0]["embedding"]) == [1.0, 0.0, 0.0]


def test_scoring_a_run_refreshes_the_card(isolated_engine, scripted_family, monkeypatch):
    """The 1-5 score lands after the run that built the card, so without a
    rebuild on that event every card would record a null user_score forever."""
    import agents.chat as chat_module
    import services

    from conftest import _seed_user_and_skill
    user = _seed_user_and_skill(isolated_engine)
    job_id, result_id = _seed_job_and_result(
        isolated_engine, user.user_id,
        decisions=[_decision(_action("proj:a", "keep", "Alpha"))],
    )
    services.rebuild_job_card(user.user_id, job_id)
    assert services.load_job_cards(user.user_id)[0]["payload"]["user_score"] is None

    agent = chat_module.ChatAgent.__new__(chat_module.ChatAgent)
    agent._record_tailor_score(str(result_id), 5)

    assert services.load_job_cards(user.user_id)[0]["payload"]["user_score"] == 5
    assert len(scripted_family) == 1, "the refresh must reuse the cached role_family"
