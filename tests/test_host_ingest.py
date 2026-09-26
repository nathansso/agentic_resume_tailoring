"""Host-filled ingestion and jobs (issue #192): ingest_schema, upsert_items,
open_job, list_jobs(status), and job-scoped rules.

The host's model does the reading; ART validates, deduplicates and stores. The
acceptance criteria are pinned here:

- a host-filled payload produces the same KG rows as the resume parser;
- a re-ingest never overwrites a `manually_edited` row;
- schema errors come back as structured errors, not exceptions.
"""

from types import SimpleNamespace
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlmodel import Session, select

from database.models import (
    Achievement, Education, Experience, JDProfile, JobDescription, Project, Skill, User,
    UserSkill,
)
from harness.contract import invoke

PROFILES = ("benchmark_profile.md", "data_science_specialist_priya_raman.md",
            "software_engineering_generalist_devon_whitaker.md")
SOURCE = "resume.md"

_FIELDS = {
    "experiences": ("title", "company", "start_date", "end_date", "description", "bullets"),
    "education": ("institution", "degree", "location", "start_date", "end_date", "gpa"),
    "achievements": ("title", "description", "issuer", "date"),
    "projects": ("name", "description", "start_date", "end_date", "repo_url", "demo_url"),
    "skills": ("name", "category", "proficiency"),
}
_KIND = {"experiences": "experience", "education": "education", "achievements": "achievement",
         "projects": "project", "skills": "skill"}


def _new_user(engine, name):
    with Session(engine) as s:
        user = User(name=name, email=f"{name.lower()}@example.com")
        s.add(user)
        s.commit()
        return user.user_id


def _extraction(fixture):
    """The fixture as the parser's extractors would hand it over: schema fields only."""
    return {section: [{k: v for k, v in item.items() if k in _FIELDS[section] and v is not None}
                      for item in getattr(fixture, section)]
            for section in _FIELDS}


def _rows(engine, uid):
    """Every KG row for a user, without ids, owners or timestamps."""
    skip = {"user_id", "created_at", "updated_at", "experience_id", "education_id",
            "achievement_id", "project_id"}
    out = {}
    with Session(engine) as s:
        for model in (Experience, Education, Achievement, Project):
            rows = s.exec(select(model).where(model.user_id == uid)).all()
            out[model.__name__] = sorted(
                (tuple(sorted((k, str(v)) for k, v in r.model_dump().items() if k not in skip))
                 for r in rows))
        out["skills"] = sorted(
            (sk.name, sk.category, us.proficiency, us.evidence_source)
            for us, sk in s.exec(select(UserSkill, Skill).join(Skill, Skill.skill_id == UserSkill.skill_id)
                                 .where(UserSkill.user_id == uid)).all())
    return out


@pytest.mark.parametrize("profile", PROFILES)
def test_a_host_filled_payload_stores_the_same_rows_as_the_parser(isolated_engine, monkeypatch, profile):
    import agents.parser as parser_module
    from eval.profile_fixture import load_profile
    from eval.tailoring_benchmark import PROFILES_DIR

    monkeypatch.setattr(parser_module, "engine", isolated_engine)
    ext = _extraction(load_profile(PROFILES_DIR / profile))

    # The parser, with its model calls replaced by the same extraction.
    parsed_uid = _new_user(isolated_engine, "Parsed")
    agent = parser_module.ResumeParserAgent.__new__(parser_module.ResumeParserAgent)
    agent.user = SimpleNamespace(user_id=parsed_uid)
    for section, method in (("experiences", "_extract_experiences"),
                            ("education", "_extract_education"),
                            ("achievements", "_extract_achievements"),
                            ("projects", "_extract_projects"), ("skills", "_extract_skills")):
        monkeypatch.setattr(agent, method, lambda _text, rows=ext[section]: [dict(r) for r in rows])
    agent.parse_and_save({"full_text": "resume", "source_file": SOURCE})

    # The host, through the contract.
    host_uid = _new_user(isolated_engine, "Hosted")
    records = [{"kind": _KIND[section], "data": item}
               for section in _FIELDS for item in ext[section]]
    out = invoke("upsert_items", host_uid, {"records": records, "source": SOURCE})
    assert out["error"] is None
    assert "invalid" not in out["counts"], out["results"]

    parsed = _rows(isolated_engine, parsed_uid)
    assert len(parsed["Experience"]) == len(ext["experiences"]) > 0 and parsed["skills"]
    assert _rows(isolated_engine, host_uid) == parsed
    # And a second identical upsert changes nothing.
    again = invoke("upsert_items", host_uid, {"records": records, "source": SOURCE})
    assert set(again["counts"]) <= {"unchanged", "merged", "filtered"}
    assert _rows(isolated_engine, host_uid) == _rows(isolated_engine, parsed_uid)


EXP = {"title": "Data Analyst", "company": "Acme", "start_date": "Jan 2025",
       "end_date": "Present", "bullets": ["Built dashboards"]}


def _upsert(uid, *records, **kw):
    return invoke("upsert_items", uid, {"records": list(records), **kw})


def _exp_row(engine, uid):
    with Session(engine) as s:
        return s.exec(select(Experience).where(Experience.user_id == uid)).one()


def test_results_carry_keys_and_statuses(isolated_engine):
    uid = _new_user(isolated_engine, "U")
    out = _upsert(uid, {"kind": "experience", "data": EXP})
    assert out["results"] == [{"index": 0, "kind": "experience", "key": "exp:data analyst|acme",
                               "status": "created", "message": None}]
    assert invoke("get_item", uid, {"key": "exp:data analyst|acme"})["record"]["end"] == "Present"
    assert _upsert(uid, {"kind": "experience", "data": EXP})["results"][0]["status"] == "unchanged"


def test_correct_overwrites_a_stale_field_and_a_plain_upsert_does_not(isolated_engine):
    uid = _new_user(isolated_engine, "U")
    _upsert(uid, {"kind": "experience", "data": EXP})
    ended = dict(EXP, end_date="Aug 2026")

    assert _upsert(uid, {"kind": "experience", "data": ended})["results"][0]["status"] == "unchanged"
    assert _exp_row(isolated_engine, uid).end_date == "Present"   # fill-blanks only

    res = _upsert(uid, {"kind": "experience", "data": ended, "correct": True})["results"][0]
    assert res["status"] == "corrected" and res["key"] == "exp:data analyst|acme"
    row = _exp_row(isolated_engine, uid)
    assert row.end_date == "Aug 2026" and not row.manually_edited


@pytest.mark.parametrize("correct", [False, True])
def test_a_reingest_never_overwrites_a_manually_edited_row(isolated_engine, correct):
    import services

    uid = _new_user(isolated_engine, "U")
    _upsert(uid, {"kind": "experience", "data": dict(EXP, bullets=[])})
    row = _exp_row(isolated_engine, uid)
    services.update_experience(uid, row.experience_id, {"end_date": "Jun 2026"})

    res = _upsert(uid, {"kind": "experience", "correct": correct,
                        "data": dict(EXP, end_date="Dec 2030", bullets=["New"])})["results"][0]
    assert res["status"] == "skipped_manual" and res["message"]
    row = _exp_row(isolated_engine, uid)
    assert (row.end_date, row.bullets, row.manually_edited) == ("Jun 2026", [], True)


def test_a_deleted_item_is_not_resurrected(isolated_engine):
    import services

    uid = _new_user(isolated_engine, "U")
    _upsert(uid, {"kind": "experience", "data": EXP})
    services.delete_experience(uid, _exp_row(isolated_engine, uid).experience_id)
    res = _upsert(uid, {"kind": "experience", "data": EXP})["results"][0]
    assert res["status"] == "skipped_tombstone"
    with Session(isolated_engine) as s:
        assert s.exec(select(Experience).where(Experience.user_id == uid)).all() == []


def test_schema_errors_are_structured_and_do_not_block_valid_records(isolated_engine):
    uid = _new_user(isolated_engine, "U")
    out = _upsert(uid,
                  {"kind": "project", "data": {"name": "Atlas", "stars": 4}},
                  {"kind": "skill", "data": {"name": "SQL", "proficiency": "expert"}},
                  {"kind": "education", "data": {"degree": "B.S."}},
                  {"kind": "project", "data": {"name": "Atlas", "description": "A map"}})
    statuses = [(r["status"], r["message"]) for r in out["results"]]
    assert statuses[0] == ("invalid", "stars: Extra inputs are not permitted")
    assert statuses[1][0] == "invalid" and statuses[1][1].startswith("proficiency:")
    assert statuses[2] == ("invalid", "institution is required.")
    assert statuses[3] == ("created", None)


def test_skills_dedupe_on_a_normalized_name(isolated_engine):
    uid = _new_user(isolated_engine, "U")
    _upsert(uid, {"kind": "skill", "data": {"name": "Time series analysis", "proficiency": 3}})
    out = _upsert(uid, {"kind": "skill", "data": {"name": "Time-Series Analysis", "proficiency": 4}},
                  {"kind": "skill", "data": {"name": "time series analysis"}},
                  {"kind": "skill", "data": {"name": "os"}})
    assert [(r["status"], r["key"]) for r in out["results"]] == [
        ("merged", "skill:time series analysis"), ("merged", "skill:time series analysis"),
        ("filtered", None)]
    assert [i["key"] for i in invoke("list_items", uid, {"kind": "skill"})["items"]] == [
        "skill:time series analysis"]


def test_ingest_schema_returns_the_extraction_schema(isolated_engine):
    uid = _new_user(isolated_engine, "U")
    out = invoke("ingest_schema", uid, {"kind": "experience"})
    assert set(out["json_schema"]["properties"]) == set(_FIELDS["experiences"])
    assert out["json_schema"]["additionalProperties"] is False
    assert out["required"] == ["company"]
    req = invoke("ingest_schema", uid, {"kind": "requirement"})["json_schema"]
    assert {"text", "type", "criticality", "terms"} <= set(req["properties"])


# ── open_job and list_jobs ───────────────────────────────────────────────────

JD = ("Data Scientist Intern\n\nRequirements\n- Experience with Python and SQL\n"
      "- Students must return to school after the internship.\n")
REQS = [{"text": "The candidate has experience with Python.", "terms": ["python"],
         "criticality": 5, "source_section": "Requirements"},
        {"text": "The candidate knows SQL.", "type": "preferred", "terms": ["sql"]}]
META = {"title": "Data Scientist Intern", "company": "Acme", "url": "https://example.com/1"}


def _open(uid, **kw):
    return invoke("open_job", uid, {"jd_text": JD, "requirements": REQS, "metadata": META, **kw})


def test_open_job_persists_the_profile_and_weights(isolated_engine):
    uid = _new_user(isolated_engine, "U")
    _upsert(uid, {"kind": "skill", "data": {"name": "Python", "proficiency": 4}})
    out = _open(uid)
    assert out["error"] is None and out["created"] and out["requirements"] == 2
    assert out["application_status"] == "drafting" and out["schema_errors"] == []
    assert "python" in {t["term"] for t in out["top_terms"]}
    with Session(isolated_engine) as s:
        job = s.get(JobDescription, UUID(out["job_id"]))
        assert (job.title, job.source_url, job.user_id) == (META["title"], META["url"], uid)
        profile = s.exec(select(JDProfile).where(JDProfile.job_id == job.job_id)).one()
        texts = [r["text"] for r in profile.payload["requirements"]]
        assert texts == [r["text"] for r in REQS]
        assert profile.payload["requirements"][1]["type"] == "preferred"
        assert profile.weights["terms"]["python"] > 0 and profile.extraction_key

    again = _open(uid)
    assert (again["job_id"], again["created"]) == (out["job_id"], False)


def test_open_job_reports_bad_requirements_and_needs_a_title_for_a_new_job(isolated_engine):
    uid = _new_user(isolated_engine, "U")
    out = _open(uid, requirements=[REQS[0], {"text": "x", "type": "must"}, {"terms": ["go"]}])
    assert out["requirements"] == 1
    assert [e["where"] for e in out["schema_errors"]] == ["requirements[1]", "requirements[2]"]
    missing = invoke("open_job", uid, {"jd_text": JD, "metadata": {"company": "Acme"}})
    assert missing["error"]["code"] == "invalid_arguments"
    unknown = invoke("open_job", uid, {"job_id": "00000000-0000-0000-0000-000000000009"})
    assert unknown["error"]["code"] == "not_found"


def test_list_jobs_filters_on_application_status(isolated_engine):
    uid = _new_user(isolated_engine, "U")
    a = _open(uid)["job_id"]
    b = _open(uid, metadata=dict(META, company="Other"))["job_id"]
    invoke("open_job", uid, {"job_id": b, "metadata": {"status": "applied"}})
    jobs = invoke("list_jobs", uid, {})["jobs"]
    assert {j["job_id"]: j["application_status"] for j in jobs} == {a: "drafting", b: "applied"}
    assert [j["job_id"] for j in invoke("list_jobs", uid, {"status": "applied"})["jobs"]] == [b]


# ── job-scoped rules ─────────────────────────────────────────────────────────

EDU = {"institution": "State University", "degree": "B.S. Statistics", "end_date": "June 2027"}
EDU_KEY = "edu:state university|b.s. statistics"
RULE = {"item_key": EDU_KEY, "field": "end_date", "value_if_yes": "Dec 2027",
        "question": "Does the posting require enrollment after the internship ends?"}


def test_a_rule_flows_from_briefing_to_open_job_to_the_executor(isolated_engine):
    from harness.executor import execute_plan

    uid = _new_user(isolated_engine, "U")
    _upsert(uid, {"kind": "education", "data": EDU})
    out = _upsert(uid, {"kind": "rule", "data": RULE})
    assert out["results"][0]["status"] == "created"
    rule_id = out["results"][0]["key"].split(":", 1)[1]
    assert _upsert(uid, {"kind": "rule", "data": RULE})["results"][0]["status"] == "unchanged"

    briefing = invoke("art_briefing", uid, {})
    assert [r["rule_id"] for r in briefing["job_rules"]] == [rule_id]

    job = _open(uid)
    assert [(r["status"], r["value"]) for r in job["rules"]] == [("needs_answer", None)]
    base = execute_plan(uid, {"job_id": job["job_id"]}, dry_run=True)
    assert base["rules_applied"] == []

    answered = invoke("open_job", uid, {"job_id": job["job_id"], "rule_answers": [
        {"rule_id": rule_id, "answer": True, "quote": "must return to school"}]})
    assert [(r["status"], r["value"], r["quote"]) for r in answered["rules"]] == [
        ("answered", "Dec 2027", "must return to school")]
    run = execute_plan(uid, {"job_id": job["job_id"]}, dry_run=True)
    assert run["rules_applied"] == [{"rule_id": rule_id, "item_key": EDU_KEY,
                                     "field": "end_date", "from": "June 2027", "to": "Dec 2027"}]

    # Answering no, with no value_if_no, leaves the stored date.
    invoke("open_job", uid, {"job_id": job["job_id"],
                             "rule_answers": [{"rule_id": rule_id, "answer": False}]})
    assert execute_plan(uid, {"job_id": job["job_id"]}, dry_run=True)["rules_applied"] == []


def test_a_rule_must_target_an_existing_education_or_experience_field(isolated_engine):
    uid = _new_user(isolated_engine, "U")
    _upsert(uid, {"kind": "education", "data": EDU})
    bad = _upsert(uid, {"kind": "rule", "data": dict(RULE, item_key="edu:nowhere|")},
                  {"kind": "rule", "data": dict(RULE, item_key="proj:x")},
                  {"kind": "rule", "data": dict(RULE, field="gpa")})
    assert [r["status"] for r in bad["results"]] == ["invalid"] * 3


def test_apply_job_rules_is_idempotent():
    from harness.ingest import apply_job_rules

    content = {"education": [{"institution": "State University", "degree": "B.S. Statistics",
                              "end_date": "June 2027"}]}
    rule = {"rule_id": "r", "item_key": EDU_KEY, "field": "end_date", "status": "answered",
            "value": "Dec 2027"}
    assert len(apply_job_rules(content, [rule])) == 1
    assert apply_job_rules(content, [rule]) == []
    assert content["education"][0]["end_date"] == "Dec 2027"


# ── schema migration ─────────────────────────────────────────────────────────

def test_init_db_adds_the_new_columns_to_an_existing_database(isolated_engine):
    import database.db as db

    if isolated_engine.dialect.name == "postgresql":
        with Session(isolated_engine) as s:
            assert s.execute(text("SELECT current_schema()")).scalar().startswith("art_test_")
    with Session(isolated_engine) as s:
        s.add(JobDescription(title="Old", company="Co"))
        s.commit()
        s.execute(text("ALTER TABLE jobdescription DROP COLUMN application_status"))
        s.execute(text("ALTER TABLE jdprofile DROP COLUMN eligibility"))
        s.commit()

    db.init_db()

    cols = lambda t: {c["name"] for c in sa.inspect(isolated_engine).get_columns(t)}  # noqa: E731
    assert "application_status" in cols("jobdescription") and "eligibility" in cols("jdprofile")
    with Session(isolated_engine) as s:
        job = s.exec(select(JobDescription)).one()
        assert job.application_status == "drafting"
