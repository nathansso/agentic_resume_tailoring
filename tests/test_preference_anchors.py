"""The preference anchor resolver (#295): `harness/anchors.py`, and `record_preference` and the memory
gate's automatic write routed through it.

The resolver is pure over a list of items, so most of the rules are tested on `build_items` records
with no store. The wiring tests use the `kg` fixture (a Python skill, a Data Science Intern role at
IDX Exchange, the Next-Item Recommendation project, a UC San Diego degree, a "1st Place Overall" award)
and add what a case needs. Jev is scripted as in `tests/test_memory_gate.py`; nothing reaches the network.
"""

import pytest
from sqlmodel import Session, select

from database.models import Experience, Project, Skill, UserPreference, UserSkill
from harness import anchors, memory, tools
from harness.anchors import AMBIGUOUS, BOUND, DOCUMENT, MULTIPLE, PENDING
from harness.decisions import engine
from harness.decisions.client import JevClient

from test_memory_gate import ScriptedJev


def _items():
    return anchors.build_items(
        skills=[{"name": n} for n in ("Python", "PyTorch", "Kafka", "R", "Go", "React", "scikit-learn",
                                      "PostgreSQL", "Kubernetes")],
        experiences=[{"title": "Data Science Intern", "company": "IDX Exchange"},
                     {"title": "Research Assistant", "company": "Agos"},
                     {"title": "Research Assistant", "company": "UCSD"}],
        projects=[{"name": "Next-Item Recommendation"}, {"name": "Kafka Streams Demo"},
                  {"name": "Google Maps Scraper"}],
        education=[{"institution": "UC San Diego", "degree": "M.S. Data Science"}],
        achievements=[{"title": "1st Place Overall"}])


def _bound(subject, items=None):
    out = anchors.resolve(items if items is not None else _items(), subject)
    assert out["status"] == BOUND, out
    return out["key"]


# ── the steps ────────────────────────────────────────────────────────────────

def test_an_alias_binds_the_skill_it_names():
    assert _bound("torch") == "skill:pytorch"
    assert _bound("sklearn") == "skill:scikit-learn"
    assert _bound("Postgres") == "skill:postgresql"
    assert _bound("scikit learn") == "skill:scikit-learn"           # hyphen and space are one separator
    assert anchors.resolve(_items(), "torch")["step"] == "alias"


def test_a_skill_stored_under_an_alias_is_found_by_its_canonical_name():
    items = anchors.build_items(skills=[{"name": "torch"}])
    assert _bound("PyTorch", items) == "skill:torch"


def test_an_exact_key_a_label_and_a_name_bind_in_that_order():
    assert anchors.resolve(_items(), "skill:python") | {} == {
        "status": BOUND, "subject": "skill:python", "key": "skill:python", "step": "key"}
    assert _bound("SKILL:PYTHON") == "skill:python"
    by_label = anchors.resolve(_items(), "skill: Python")
    assert (by_label["key"], by_label["step"]) == ("skill:python", "label")
    by_name = anchors.resolve(_items(), "python")
    assert (by_name["key"], by_name["step"]) == ("skill:python", "name")
    assert _bound("exp:data science intern|idx exchange") == "exp:data science intern|idx exchange"
    assert _bound("proj:next-item recommendation") == "proj:next-item recommendation"
    assert _bound("project: Next-Item Recommendation") == "proj:next-item recommendation"
    assert _bound("Next-Item Recommendation") == "proj:next-item recommendation"


def test_a_role_binds_by_title_or_both_and_a_company_alone_stays_pending():
    both = "exp:data science intern|idx exchange"
    assert _bound("Data Science Intern") == both
    assert _bound("Data Science Intern @ IDX Exchange") == both
    # the company is not the role's name: a term pin catches every mention, an item pin would not
    assert anchors.resolve(_items(), "IDX Exchange")["status"] == PENDING


def test_education_and_achievements_resolve_by_key_label_and_name():
    assert _bound("UC San Diego") == "edu:uc san diego|m.s. data science"
    assert _bound("edu:uc san diego|m.s. data science") == "edu:uc san diego|m.s. data science"
    assert _bound("1st Place Overall") == "ach:1st place overall"
    assert _bound("achievement: 1st Place Overall") == "ach:1st place overall"
    assert _bound("ach:1st place overall") == "ach:1st place overall"


def test_several_matches_at_one_step_are_ambiguous_with_their_candidates():
    out = anchors.resolve(_items(), "Research Assistant")
    assert out["status"] == AMBIGUOUS and out["step"] == "name"
    assert {c["key"] for c in out["candidates"]} == {"exp:research assistant|agos", "exp:research assistant|ucsd"}
    assert "key" not in out                                       # none of them is picked
    twin = anchors.build_items(skills=[{"name": "Kafka"}], projects=[{"name": "Kafka"}])
    out = anchors.resolve(twin, "Kafka")
    assert out["status"] == AMBIGUOUS
    assert {c["key"] for c in out["candidates"]} == {"skill:kafka", "proj:kafka"}


def test_an_earlier_step_wins_over_a_later_one():
    # "Go" is the key's name for a skill and a project's bare name, but the key is exact
    items = anchors.build_items(skills=[{"name": "Go"}], projects=[{"name": "skill:go"}])
    assert _bound("skill:go", items) == "skill:go"


# ── what is not a match ──────────────────────────────────────────────────────

@pytest.mark.parametrize("subject,held,not_bound", [
    ("Kafka", "Kafka Streams", "skill:kafka streams"),
    ("R", "React", "skill:react"),
    ("Go", "Google", "skill:google"),
    ("Java", "JavaScript", "skill:javascript"),
    ("C", "C++", "skill:c++"),
    ("Spark", "PySpark", "skill:pyspark"),
])
def test_a_substring_never_binds(subject, held, not_bound):
    items = anchors.build_items(skills=[{"name": held}])
    out = anchors.resolve(items, subject)
    assert out["status"] == PENDING and "key" not in out
    reverse = anchors.build_items(skills=[{"name": subject}])
    assert anchors.resolve(reverse, held)["status"] == PENDING


def test_a_short_name_binds_only_its_own_skill():
    items = _items()
    assert _bound("R", items) == "skill:r"
    assert _bound("Go", items) == "skill:go"
    assert anchors.resolve(items, "Re")["status"] == PENDING       # a fragment of React
    assert anchors.resolve(items, "Goo")["status"] == PENDING
    # no short alias: "tf" is also a fragment of TF-IDF, so it does not reach TensorFlow
    tf = anchors.build_items(skills=[{"name": "TensorFlow"}])
    assert anchors.resolve(tf, "tf")["status"] == PENDING
    assert _bound("TensorFlow", tf) == "skill:tensorflow"


def test_a_kafka_ban_with_a_kafka_streams_item_stays_pending_until_kafka_is_there():
    items = anchors.build_items(skills=[{"name": "Kafka Streams"}], projects=[{"name": "Kafka Streams Demo"}])
    assert anchors.resolve(items, "Kafka")["status"] == PENDING
    assert _bound("Kafka", items + anchors.build_items(skills=[{"name": "Kafka"}])) == "skill:kafka"


# ── pending, document, multiple ──────────────────────────────────────────────

@pytest.mark.parametrize("subject", ["Terraform", "my startup", "GPA", "proj:old-repo", "Apache Flink"])
def test_a_nameable_thing_the_graph_lacks_is_pending(subject):
    out = anchors.resolve(_items(), subject)
    assert out["status"] == PENDING and out["subject"] == subject and "key" not in out


@pytest.mark.parametrize("subject,key", [
    ("section: projects", "section:projects"),
    ("section:achievements", "section:achievements"),
    ("Section: Education", "section:education"),
    ("achievements", "section:achievements"),
    ("section: awards", None),
    ("keep it to one page", None),
    ("page length", None),
    ("the order of my sections", None),
    ("tone", None),
    ("wording of the summary", None),
])
def test_a_subject_about_the_page_is_a_document(subject, key):
    out = anchors.resolve(_items(), subject)
    assert out["status"] == DOCUMENT and out.get("key") == key


def test_an_item_named_like_a_document_word_is_still_that_item():
    items = anchors.build_items(projects=[{"name": "Order Management System"}])
    assert _bound("Order Management System", items) == "proj:order management system"


@pytest.mark.parametrize("subject,parts", [
    ("Kafka or Spark", ["Kafka", "Spark"]),
    ("Kafka and Spark", ["Kafka", "Spark"]),
    ("Kafka, Spark", ["Kafka", "Spark"]),
    ("Kafka & Spark & Flink", ["Kafka", "Spark", "Flink"]),
    ("Kafka / Spark", ["Kafka", "Spark"]),
    ("Python and my startup", ["Python", "my startup"]),
])
def test_several_subjects_are_refused_and_listed(subject, parts):
    out = anchors.resolve(_items(), subject)
    assert out["status"] == MULTIPLE and out["subjects"] == parts and "key" not in out


def test_one_name_with_a_joining_character_is_not_several():
    items = anchors.build_items(skills=[{"name": "C/C++"}, {"name": "R&D"}])
    assert _bound("C/C++", items) == "skill:c/c++"
    assert anchors.resolve([], "Q&A")["status"] == PENDING
    assert anchors.resolve([], "Black & Decker")["status"] == MULTIPLE      # spaces around & join two


def test_an_empty_subject_binds_nothing():
    assert anchors.resolve(_items(), "")["status"] == PENDING
    assert anchors.resolve(_items(), None)["status"] == PENDING


# ── a message ────────────────────────────────────────────────────────────────

def test_a_message_that_names_one_item_binds_it_and_two_are_ambiguous():
    assert anchors.mentions(_items(), "Always lead with Python in my skills")["key"] == "skill:python"
    assert anchors.mentions(_items(), "Put torch before everything")["key"] == "skill:pytorch"
    two = anchors.mentions(_items(), "Put Python before PyTorch")
    assert two["status"] == AMBIGUOUS and len(two["candidates"]) == 2
    assert anchors.mentions(_items(), "Lead with the cloud stuff")["status"] == PENDING
    # R is not inside "Research", Go is not the verb that opens a sentence
    assert anchors.mentions(anchors.build_items(skills=[{"name": "R"}]), "Research comes first")["status"] == PENDING
    assert anchors.mentions(anchors.build_items(skills=[{"name": "Go"}]), "Go to the top")["status"] == PENDING


# ── record_preference ────────────────────────────────────────────────────────

def _add_skill(engine_, uid, name):
    with Session(engine_) as s:
        skill = Skill(name=name, category="tool")
        s.add(skill)
        s.commit()
        s.refresh(skill)
        s.add(UserSkill(user_id=uid, skill_id=skill.skill_id, proficiency=4, evidence_source="resume",
                        confidence_score=0.9))
        s.commit()


def _prefs(engine_, uid, **where):
    with Session(engine_) as s:
        rows = s.exec(select(UserPreference).where(UserPreference.user_id == uid)).all()
    return [r for r in rows if all(getattr(r, k) == v for k, v in where.items())]


def test_record_preference_binds_an_alias_to_the_skill_it_names(kg, isolated_engine):
    _add_skill(isolated_engine, kg, "PyTorch")
    out = memory.record_preference(kg, "Never mention torch", "suppress", "torch", 5)
    assert out["status"] == "stored" and out["target_key"] == "skill:pytorch" and out["target_resolved"]
    assert out["anchor"] == {"status": "bound", "key": "skill:pytorch", "step": "alias"}
    assert out["negative_pin"] and out["suggestions"] == []
    [row] = _prefs(isolated_engine, kg, target_key="skill:pytorch")
    assert row.polarity == "suppress" and row.strength == 5


def test_record_preference_refuses_an_ambiguous_subject_with_its_candidates(kg, isolated_engine):
    with Session(isolated_engine) as s:
        s.add(Experience(user_id=kg, title="Data Science Intern", company="Rippling"))
        s.commit()
    before = len(_prefs(isolated_engine, kg))
    out = memory.record_preference(kg, "Never mention the internship", "suppress", "Data Science Intern", 5)
    assert out["status"] == "refused" and out["anchor"] == {
        "status": "ambiguous", "step": "name", "candidates": out["candidates"]}
    assert {c["key"] for c in out["candidates"]} == {
        "exp:data science intern|idx exchange", "exp:data science intern|rippling"}
    assert len(_prefs(isolated_engine, kg)) == before
    # naming one by its key stores it
    ok = memory.record_preference(kg, "Never mention the internship", "suppress",
                                  "exp:data science intern|rippling", 5)
    assert ok["status"] == "stored" and ok["target_key"] == "exp:data science intern|rippling"


def test_record_preference_refuses_several_subjects_and_stores_nothing(kg, isolated_engine):
    before = len(_prefs(isolated_engine, kg))
    out = memory.record_preference(kg, "Never mention Kafka or Spark", "suppress", "Kafka or Spark", 5)
    assert out["status"] == "refused" and out["subjects"] == ["Kafka", "Spark"]
    assert out["anchor"]["status"] == "multiple" and "one preference per subject" in out["reason"]
    assert len(_prefs(isolated_engine, kg)) == before


def test_an_unknown_thing_is_stored_pending_with_its_term(kg, isolated_engine):
    out = memory.record_preference(kg, "Never mention Kafka", "suppress", "Kafka", 5)
    assert out["status"] == "stored" and out["anchor"] == {"status": "pending"}
    assert out["target_key"] is None and out["target_term"] == "Kafka" and out["negative_pin"]


def test_a_document_subject_reports_its_status_and_is_stored_as_before(kg):
    section = memory.record_preference(kg, "Skip achievements", "suppress", "section: achievements", 3)
    assert section["anchor"] == {"status": "document", "key": "section:achievements"}
    assert section["target_key"] == "section:achievements"
    page = memory.record_preference(kg, "Keep it to one page", "reframe", "page length", 4)
    assert page["status"] == "stored" and page["anchor"] == {"status": "document"}
    assert page["target_key"] is None and page["target_term"] == "page length"
    bare = memory.record_preference(kg, "Format as one page", "reframe", None, 4)
    assert bare["status"] == "stored" and "anchor" in bare and bare["anchor"] is None


def test_education_and_an_award_resolve_through_record_preference_and_stay_enforceable(kg):
    edu = memory.record_preference(kg, "Leave my degree off bullets", "suppress", "UC San Diego", 5)
    assert edu["target_key"] == "edu:uc san diego|m.s. data science" and edu["target_resolved"]
    assert edu["target_term"] == "UC San Diego" and edu["negative_pin"]
    ach = memory.record_preference(kg, "Never mention the hackathon win", "suppress", "1st Place Overall", 5)
    assert ach["target_key"] == "ach:1st place overall" and ach["target_term"] == "1st Place Overall"
    pins = {p["target_key"]: p for p in tools.art_pins(kg)["pins"]}
    assert pins["ach:1st place overall"]["negative_pin"]


def test_the_memory_gates_catalog_is_unchanged_by_education_and_achievements(kg):
    kinds = {c["target_type"] for c in memory.memory_catalog(kg)}
    assert kinds == {"section", "experience", "project", "skill"}


# ── the gate's automatic write ───────────────────────────────────────────────

@pytest.fixture()
def jev(monkeypatch, isolated_engine):
    fake = ScriptedJev()
    monkeypatch.setenv("ART_JEV_MODE", "auto")
    monkeypatch.setattr(engine, "get_client", lambda: JevClient("k", transport=fake, sleep=lambda s: None))
    return fake


def test_the_gate_writes_when_jev_and_the_resolver_name_the_same_item(kg, jev, isolated_engine):
    msg = jev.say("Please lead with Python in my skills.", direction="emphasize", level=3.0,
                  target="skill: Python")
    d = memory.observe(kg, msg, "anchor-session")
    assert d["action"] == "write" and d["reason"] == "auto"
    assert len(_prefs(isolated_engine, kg, target_key="skill:python", text=msg)) == 1


def test_the_gate_does_not_write_a_key_the_resolver_disagrees_with(kg, jev, isolated_engine):
    # the message names two skills; Jev picks one of them and names_target is satisfied, but the
    # resolver finds two items, so the gate asks the host
    _add_skill(isolated_engine, kg, "SQL")
    msg = jev.say("Please lead with Python before SQL in my skills.", direction="emphasize", level=3.0,
                  target="skill: Python")
    assert anchors.mentions(kg, msg)["status"] == AMBIGUOUS
    before = len(_prefs(isolated_engine, kg))
    d = memory.observe(kg, msg, "anchor-session")
    assert (d["action"], d["reason"]) == ("host", "anchor_disagrees")
    assert d["anchor"]["status"] == "ambiguous" and "preference_id" not in d
    assert "confirms which item" in d["note"]
    assert len(_prefs(isolated_engine, kg)) == before


def test_the_gate_does_not_write_when_the_resolver_finds_another_item(kg, jev, isolated_engine, monkeypatch):
    msg = jev.say("Please lead with Python in my skills.", direction="emphasize", level=3.0,
                  target="skill: Python")
    monkeypatch.setattr(anchors, "mentions", lambda *a, **k: {"status": BOUND, "key": "proj:next-item recommendation"})
    before = len(_prefs(isolated_engine, kg))
    d = memory.observe(kg, msg, "anchor-session")
    assert (d["action"], d["reason"]) == ("host", "anchor_disagrees")
    assert len(_prefs(isolated_engine, kg)) == before
