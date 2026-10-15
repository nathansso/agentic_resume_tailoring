"""Preferences follow the graph (#297): re-link on ingest, follow renames and deletes, ride on the item.

A preference about something the graph lacks is stored pending (`target_key` NULL, `target_term` the
user's words, #295). These tests cover what keeps keys true afterwards: `harness/relink.py` run after
`upsert_items`, `services.update_*` and `delete_*` re-keying or unlinking the rows that point at an
item, and `get_item` / `kg_search` / `list_items` returning an item's preferences and job rules.

The `kg` fixture is a Python skill, a Data Science Intern role at IDX Exchange, the Next-Item
Recommendation project, a UC San Diego degree and a "1st Place Overall" award. The executor test uses
the benchmark profile (`test_executor.env`). Nothing reaches the network.
"""

import pytest
from sqlmodel import Session, select

import services
from database.models import Education, Experience, Project, Skill, UserPreference, UserSkill
from harness import ingest, memory, relink, tools
from harness.decisions.negative_pins import pin_target

from test_executor import EXP2, _run, env  # noqa: F401  (fixture)
from test_negative_pin_gate import run_weave  # noqa: F401


def _prefs(engine, uid):
    with Session(engine) as s:
        return s.exec(select(UserPreference).where(UserPreference.user_id == uid)).all()


def _active(engine, uid, **match):
    return [p for p in _prefs(engine, uid)
            if p.status == "active" and all(getattr(p, k) == v for k, v in match.items())]


def _id(engine, model, uid, **match):
    """The id of the user's row of `model` with these fields, as the router receives it."""
    with Session(engine) as s:
        row = next(r for r in s.exec(select(model).where(model.user_id == uid)).all()
                   if all(getattr(r, k) == v for k, v in match.items()))
        return str(getattr(row, {Experience: "experience_id", Education: "education_id",
                                 Project: "project_id"}[model]))


def _upsert(uid, *records):
    return ingest.upsert_items(uid, [{"kind": k, "data": d} for k, d in records])


def _ban(uid, subject, **kw):
    out = memory.record_preference(uid, f"Never mention {subject}", "suppress", subject, 5, **kw)
    assert out["status"] in ("stored", "superseded"), out
    return out


def _skill(name):
    return ("skill", {"name": name})


# ── re-link on ingest ────────────────────────────────────────────────────────

def test_a_ban_set_before_the_item_exists_binds_when_ingestion_creates_it(kg, isolated_engine):
    out = _ban(kg, "Kafka")
    assert out["anchor"]["status"] == "pending" and out["target_key"] is None
    [pending] = _active(isolated_engine, kg, target_term="Kafka")
    assert pending.target_key is None

    done = _upsert(kg, _skill("Kafka"))

    [moved] = done["relinked"]
    assert moved["key"] == "skill:kafka" and moved["supersedes_id"] == str(pending.preference_id)
    [keyed] = _active(isolated_engine, kg, target_key="skill:kafka")
    assert (keyed.text, keyed.polarity, keyed.strength, keyed.scope_type, keyed.target_type) == (
        pending.text, "suppress", 5, "global", "skill")
    assert keyed.supersedes_id == pending.preference_id
    assert keyed.provenance["relinked_from"] == str(pending.preference_id)
    with Session(isolated_engine) as s:                      # nothing deleted: the old row is superseded
        assert s.get(UserPreference, pending.preference_id).status == "superseded"
    assert pin_target({"strength": 5, "polarity": "suppress", "target_key": "skill:kafka"}) == ("skill", "kafka")
    assert "skill:kafka" in [p["target_key"] for p in tools.art_pins(kg)["pins"]]


def test_the_relinked_ban_still_refuses_a_bullet_in_another_item(env, isolated_engine):
    uid, *_ = env
    with Session(isolated_engine) as s:           # the benchmark profile holds Kafka: take it away
        kafka = s.exec(select(Skill).where(Skill.name == "Kafka")).first()
        for link in s.exec(select(UserSkill).where(UserSkill.user_id == uid,
                                                   UserSkill.skill_id == kafka.skill_id)).all():
            s.delete(link)
        s.commit()
    assert _ban(uid, "Kafka")["anchor"]["status"] == "pending"

    assert [r["key"] for r in _upsert(uid, _skill("Kafka"))["relinked"]] == ["skill:kafka"]

    _, _, ran = run_weave(env, dry_run=True)                       # the woven bullet says "using Kafka"
    node = ran["nodes"][0]
    assert node["status"] == "refused" and node["reason"].startswith("negative_pin: bullet 3 mentions 'kafka'")
    assert [p.target_key for p in _active(isolated_engine, uid, target_key="skill:kafka")] == ["skill:kafka"]


def test_relinking_again_changes_nothing(kg, isolated_engine):
    _ban(kg, "Kafka")
    _upsert(kg, _skill("Kafka"))
    before = {(p.preference_id, p.status) for p in _prefs(isolated_engine, kg)}

    assert relink.relink(kg) == {"relinked": [], "count": 0}
    assert _upsert(kg, _skill("Kafka"))["relinked"] == []
    assert {(p.preference_id, p.status) for p in _prefs(isolated_engine, kg)} == before


def test_kafka_streams_does_not_bind_a_kafka_ban(kg, isolated_engine):
    _ban(kg, "Kafka")

    assert _upsert(kg, _skill("Kafka Streams"))["relinked"] == []

    [still] = _active(isolated_engine, kg, target_term="Kafka")
    assert still.target_key is None and not still.provenance.get("relinked_from")


def test_a_subject_that_fits_two_items_stays_pending(kg, isolated_engine):
    with Session(isolated_engine) as s:
        s.add(UserPreference(user_id=kg, text="Never mention the research assistant role",
                             polarity="suppress", target_term="Research Assistant",
                             scope_type="global", strength=5))
        s.commit()

    done = _upsert(kg, ("experience", {"title": "Research Assistant", "company": "Agos"}),
                   ("experience", {"title": "Research Assistant", "company": "UCSD"}))

    assert done["counts"].get("created") == 2 and done["relinked"] == []
    [p] = _active(isolated_engine, kg, target_term="Research Assistant")
    assert p.target_key is None


def test_a_role_family_scoped_ban_keeps_its_scope_and_an_education_stays_a_topic(kg, isolated_engine):
    _ban(kg, "Kafka", scope="role_family", scope_value="data_science")
    with Session(isolated_engine) as s:
        s.add(UserPreference(user_id=kg, text="Leave out the Oxford degree", polarity="suppress",
                             target_term="University of Oxford", scope_type="global", strength=5))
        s.commit()

    done = _upsert(kg, _skill("Kafka"), ("education", {"institution": "University of Oxford",
                                                       "degree": "B.A. Philosophy"}))

    assert {r["key"] for r in done["relinked"]} == {"skill:kafka", "edu:university of oxford|b.a. philosophy"}
    [kafka] = _active(isolated_engine, kg, target_key="skill:kafka")
    assert (kafka.scope_type, kafka.scope_value) == ("role_family", "data_science")
    [edu] = _active(isolated_engine, kg, target_key="edu:university of oxford|b.a. philosophy")
    assert edu.target_type == "topic" and edu.target_term == "University of Oxford"


def test_a_preference_that_is_not_about_a_thing_is_left_alone(kg, isolated_engine):
    with Session(isolated_engine) as s:
        s.add(UserPreference(user_id=kg, text="Keep it to one page", polarity="reframe",
                             target_term="Kafka", scope_type="global", strength=3))
        s.commit()
    assert _upsert(kg, _skill("Kafka"))["relinked"] == []


# ── renames ──────────────────────────────────────────────────────────────────

def test_renaming_a_project_re_keys_its_preference(kg, isolated_engine):
    out = memory.record_preference(kg, "Never show the recommender", "suppress", "Next-Item Recommendation", 5)
    assert out["target_key"] == "proj:next-item recommendation"
    [old] = _active(isolated_engine, kg, target_key="proj:next-item recommendation")
    with Session(isolated_engine) as s:
        pid = str(s.exec(select(Project).where(Project.user_id == kg)).first().project_id)

    services.update_project(kg, pid, {"name": "Session Recommender"})

    assert _active(isolated_engine, kg, target_key="proj:next-item recommendation") == []
    [new] = _active(isolated_engine, kg, target_key="proj:session recommender")
    assert new.supersedes_id == old.preference_id and new.strength == 5 and new.text == old.text
    assert new.provenance["renamed_from"] == "proj:next-item recommendation"
    assert pin_target({"strength": 5, "polarity": "suppress", "target_key": new.target_key}) == (
        "item", "proj:session recommender")
    with Session(isolated_engine) as s:
        assert s.get(UserPreference, old.preference_id).status == "superseded"


def test_a_rename_that_keeps_the_key_changes_nothing(kg, isolated_engine):
    memory.record_preference(kg, "Never show the recommender", "suppress", "Next-Item Recommendation", 5)
    before = len(_prefs(isolated_engine, kg))
    with Session(isolated_engine) as s:
        pid = str(s.exec(select(Project).where(Project.user_id == kg)).first().project_id)
    services.update_project(kg, pid, {"description": "A new description."})
    assert len(_prefs(isolated_engine, kg)) == before


def test_renaming_a_role_re_keys_its_preference(kg, isolated_engine):
    memory.record_preference(kg, "Never show the internship", "suppress", "Data Science Intern", 5)
    services.update_experience(kg, _id(isolated_engine, Experience, kg), {"title": "Data Scientist"})

    assert [p.target_key for p in _active(isolated_engine, kg, strength=5)
            if p.text == "Never show the internship"] == ["exp:data scientist|idx exchange"]


def test_a_corrected_degree_re_keys_the_education_preference(kg, isolated_engine):
    memory.record_preference(kg, "Never mention my degree", "suppress", "UC San Diego", 5)
    [old] = _active(isolated_engine, kg, target_key="edu:uc san diego|m.s. data science")

    done = ingest.upsert_items(kg, [{"kind": "education", "correct": True,
                                     "data": {"institution": "UC San Diego",
                                              "degree": "M.S. Data Science and Engineering"}}])
    assert done["results"][0]["status"] == "corrected"

    [new] = _active(isolated_engine, kg, target_key="edu:uc san diego|m.s. data science and engineering")
    assert new.supersedes_id == old.preference_id and new.target_term == old.target_term
    assert new.provenance["renamed_from"] == "edu:uc san diego|m.s. data science"


# ── deletes ──────────────────────────────────────────────────────────────────

def test_deleting_the_item_returns_the_ban_to_pending_and_it_still_blocks(kg, isolated_engine):
    memory.record_preference(kg, "Never show the recommender", "suppress", "Next-Item Recommendation", 5)
    [keyed] = _active(isolated_engine, kg, target_key="proj:next-item recommendation")
    with Session(isolated_engine) as s:
        pid = str(s.exec(select(Project).where(Project.user_id == kg)).first().project_id)

    assert services.delete_project(kg, pid)

    assert _active(isolated_engine, kg, target_key="proj:next-item recommendation") == []
    [pending] = _active(isolated_engine, kg, target_term="Next-Item Recommendation")
    assert pending.target_key is None and pending.supersedes_id == keyed.preference_id
    assert pending.provenance["unlinked_from"] == "proj:next-item recommendation"
    assert pending.strength == 5 and pending.text == keyed.text
    # a term pin: what it names still cannot reach the page
    assert pin_target({"strength": 5, "polarity": "suppress", "target_key": None,
                       "target_term": pending.target_term}) == ("term", "next-item recommendation")
    assert [p["negative_pin"] for p in tools.art_pins(kg)["pins"]
            if p["target_term"] == "Next-Item Recommendation"] == [True]


def test_deleting_a_role_pends_the_ban_under_its_title_and_company(kg, isolated_engine):
    memory.record_preference(kg, "Never mention the internship", "suppress", "Data Science Intern", 5)
    assert services.delete_experience(kg, _id(isolated_engine, Experience, kg))

    [pending] = _active(isolated_engine, kg, target_key=None, strength=5, polarity="suppress",
                        text="Never mention the internship")
    assert pending.target_term == "Data Science Intern @ IDX Exchange"
    assert pending.provenance["unlinked_from"] == "exp:data science intern|idx exchange"


def test_deleting_an_education_keeps_the_users_own_words(kg, isolated_engine):
    memory.record_preference(kg, "Never mention my degree", "suppress", "UC San Diego", 5)
    assert services.delete_education(kg, _id(isolated_engine, Education, kg))

    [pending] = _active(isolated_engine, kg, target_key=None, text="Never mention my degree")
    assert pending.target_term == "UC San Diego"


def test_a_deleted_roles_ban_does_not_block_another_role_with_the_same_title(env, isolated_engine):
    """The ban was on one role. Deleting it must leave a term that cannot match the other."""
    uid, job_id, _ = env
    _upsert(uid, ("experience", {"title": "Data Scientist", "company": "Acme"}),
            ("experience", {"title": "Data Scientist", "company": "Nimbus"}))
    memory.record_preference(uid, "Never show the Acme role", "suppress", "exp:data scientist|acme", 5)
    assert services.delete_experience(uid, _id(isolated_engine, Experience, uid, company="Acme"))

    [pending] = _active(isolated_engine, uid, target_key=None, text="Never show the Acme role")
    assert pending.target_term == "Data Scientist @ Acme"
    assert pin_target({"strength": 5, "polarity": "suppress", "target_key": None,
                       "target_term": pending.target_term}) == ("term", "data scientist @ acme")

    # what the executor enforces: the other role, and a bullet that says "data scientist", pass
    from harness.acceptance import negative_pin_violations
    from harness.executor import _hard_preferences
    _, _, terms = _hard_preferences(uid, job_id)
    assert "data scientist @ acme" in terms and "data scientist" not in terms
    nimbus = {"experiences": [{"title": "Data Scientist", "company": "Nimbus",
                               "bullets": ["Worked as a data scientist on forecasting."]}]}
    assert negative_pin_violations(nimbus, terms) == []


def test_an_unlinked_ban_comes_back_only_to_its_own_item(kg, isolated_engine):
    """Deleting one of two same-titled roles must not hand its ban to the other, even by name."""
    _upsert(kg, ("experience", {"title": "Research Assistant", "company": "Agos"}))
    memory.record_preference(kg, "Never mention the Agos role", "suppress", "exp:research assistant|agos", 5)
    assert services.delete_experience(kg, _id(isolated_engine, Experience, kg, company="Agos"))
    _upsert(kg, ("experience", {"title": "Research Assistant", "company": "UCSD"}))   # same title, other role

    assert relink.relink(kg)["relinked"] == []
    [p] = _active(isolated_engine, kg, target_key=None, text="Never mention the Agos role")
    assert p.target_term == "Research Assistant @ Agos"


def test_an_unlinked_ban_binds_again_when_its_own_item_comes_back(kg, isolated_engine):
    with Session(isolated_engine) as s:
        s.add(UserPreference(user_id=kg, text="Never mention Kafka", polarity="suppress",
                             target_term="Kafka", scope_type="global", strength=5,
                             provenance={"unlinked_from": "skill:kafka"}))
        s.commit()

    assert [r["key"] for r in _upsert(kg, _skill("Kafka"))["relinked"]] == ["skill:kafka"]


# ── items carry their preferences ────────────────────────────────────────────

def _rule(uid):
    out = _upsert(uid, ("rule", {"item_key": "edu:uc san diego|m.s. data science", "field": "degree",
                                 "question": "Does the posting require enrollment after the internship?",
                                 "value_if_yes": "M.S. Data Science (expected)"}))
    assert out["results"][0]["status"] == "created", out


def test_get_item_lists_its_preferences_and_job_rules(kg):
    memory.record_preference(kg, "Never mention Python on the page", "suppress", "Python", 5)
    memory.record_preference(kg, "For ML roles lead with Python", "emphasize", "Python", 3,
                             scope="role_family", scope_value="machine_learning")
    memory.record_preference(kg, "Never mention the degree", "suppress", "UC San Diego", 4)
    _rule(kg)

    python = tools.get_item(kg, "skill:python")
    assert [(p["polarity"], p["strength"], p["scope_type"], p["scope_value"], p["negative_pin"])
            for p in python["preferences"]] == [
        ("suppress", 5, "global", None, True), ("emphasize", 3, "role_family", "machine_learning", False)]
    assert all(p["target_key"] == "skill:python" and p["preference_id"] and p["text"]
               for p in python["preferences"])
    assert python["job_rules"] == []

    edu = tools.get_item(kg, "edu:uc san diego|m.s. data science")
    assert [p["text"] for p in edu["preferences"]] == ["Never mention the degree"]
    assert [(r["field"], r["item_key"]) for r in edu["job_rules"]] == [
        ("degree", "edu:uc san diego|m.s. data science")]

    plain = tools.get_item(kg, "proj:next-item recommendation")
    assert plain["preferences"] == [] and plain["job_rules"] == []


def test_a_superseded_or_pending_preference_is_on_no_item(kg):
    memory.record_preference(kg, "Never mention Python", "suppress", "Python", 5)
    memory.record_preference(kg, "Actually, lead with Python", "emphasize", "Python", 4)   # supersedes it
    _ban(kg, "Kafka")                                                                      # pending: no item

    assert [p["polarity"] for p in tools.get_item(kg, "skill:python")["preferences"]] == ["emphasize"]
    everything = [p for i in tools.list_items(kg) for p in i["preferences"]]
    assert [p["target_key"] for p in everything] == ["skill:python"]


def test_kg_search_and_list_items_carry_them_too(kg):
    memory.record_preference(kg, "Never mention the degree", "suppress", "UC San Diego", 4)
    _rule(kg)

    [hit] = [h for h in tools.kg_search(kg, "San Diego") if h["key"].startswith("edu:")]
    assert [p["text"] for p in hit["preferences"]] == ["Never mention the degree"]
    assert len(hit["job_rules"]) == 1

    listed = {i["key"]: i for i in tools.list_items(kg)}
    assert [p["text"] for p in listed["edu:uc san diego|m.s. data science"]["preferences"]] == [
        "Never mention the degree"]
    assert len(listed["edu:uc san diego|m.s. data science"]["job_rules"]) == 1
    assert listed["skill:python"]["preferences"] == [] and listed["skill:python"]["job_rules"] == []


def test_the_contract_validates_the_new_fields(kg):
    from harness.contract import invoke

    memory.record_preference(kg, "Never mention the degree", "suppress", "UC San Diego", 4)
    got = invoke("get_item", kg, {"key": "edu:uc san diego|m.s. data science"})
    assert got["error"] is None and got["preferences"][0]["negative_pin"] is False
    assert all("preferences" in i and "job_rules" in i for i in invoke("list_items", kg, {})["items"])
    assert all("preferences" in h for h in invoke("kg_search", kg, {"query": "python"})["results"])
    up = invoke("upsert_items", kg, {"records": [{"kind": "skill", "data": {"name": "Kafka"}}]})
    assert up["relinked"] == []
