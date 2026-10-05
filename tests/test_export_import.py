"""`art export` / `art import` and the one-time web-app migration (#195).

Every row here is synthetic. Nothing reaches a real database: the stores are
temp SQLite files and, on the Postgres leg, throwaway `art_test_*` schemas on
`ART_TEST_DATABASE_URL`, which stand in for the hosted source.
"""

import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4, uuid5, NAMESPACE_DNS

import pytest
import sqlalchemy as sa
from sqlmodel import Session, SQLModel, create_engine

from conftest import ART_TEST_DATABASE_URL, requires_postgres
from database import models as m
from harness import export_import as ei
from harness.export_import import PortError

ROOT = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)


def _id(name):
    return uuid5(NAMESPACE_DNS, f"art-export-test/{name}")


UID = _id("user")
FAKE_PASSWORD_HASH = "pbkdf2:fake-hash-" + "q" * 24
FAKE_GH_TOKEN = "ghp_" + "A1b2C3d4E5" * 4
FAKE_API_KEY = "sk-ant-" + "Zy9Xw8Vu7T" * 3
FAKE_DB_URL = "postgresql://svc_user:hunter2pass@db.example.test:5432/prod"


@pytest.fixture(autouse=True)
def _own_pointer(tmp_path, monkeypatch):
    """`art import` reads (and with --set-active writes) the profile pointer; keep it
    out of the developer's ~/.art."""
    import database.user_utils as uu
    monkeypatch.setattr(uu, "ACTIVE_PROFILE_FILE", tmp_path / "pointer" / "active_profile_id")


def _sqlite(tmp_path, name="dest"):
    engine = create_engine(f"sqlite:///{tmp_path / (name + '.db')}",
                           connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


def seed(engine, uid=UID, email="ada@example.org", with_secrets=True):
    """One profile touching every exported table. Another `uid` gets ids of its own."""
    mine = uid == UID
    tag = "" if mine else str(uid)[:6]
    n0 = 0 if mine else 100

    def i(name):
        return _id(name) if mine else _id(f"{uid}/{name}")

    with Session(engine) as s:
        s.add(m.User(
            user_id=uid, name="Ada Example", email=email, username="ada" + tag,
            password_hash=FAKE_PASSWORD_HASH if with_secrets else None,
            github_access_token=FAKE_GH_TOKEN if with_secrets else None,
            supabase_uid="sb-" + "0" * 8 + tag, location="San Jose", onboarding_complete=True,
            onboarding_steps={"resume": True}, resume_style={"font": "serif"},
            created_at=T0, updated_at=T0))
        s.add(m.Skill(skill_id=i("sk-py"), name="Python", category="language",
                      embedding="[0.1, 0.2]", embedding_model="m", created_at=T0, updated_at=T0))
        s.add(m.Skill(skill_id=i("sk-sql"), name="SQL", created_at=T0, updated_at=T0))
        s.commit()
        s.add(m.Experience(experience_id=i("exp"), user_id=uid, title="Data Intern",
                           company="Acme Analytics", start_date="2025-06", end_date="2025-09",
                           bullets=["Built a churn model", "Wrote the nightly ETL"],
                           manually_edited=True, seq=0, created_at=T0, updated_at=T0))
        s.add(m.Education(education_id=i("edu"), user_id=uid, institution="Example University",
                          degree="M.S. Data Science", end_date="June 2027", seq=0,
                          created_at=T0, updated_at=T0))
        s.commit()
        s.add(m.Project(project_id=i("proj"), user_id=uid, name="Next-Item Recommender",
                        repo_url="https://example.test/r", metrics={"stars": 3}, seq=0,
                        experience_id=i("exp"), context_status="linked",
                        created_at=T0, updated_at=T0))
        s.commit()
        s.add(m.Achievement(achievement_id=i("ach"), user_id=uid, title="1st Place",
                            issuer="Example Hack", seq=0, project_id=i("proj"),
                            created_at=T0, updated_at=T0))
        s.add(m.ProjectBlurb(blurb_id=i("blurb"), project_id=i("proj"), style="metrics",
                             content="Compared two rankers", created_at=T0))
        s.add(m.UserSkill(user_skill_id=i("us"), user_id=uid, skill_id=i("sk-py"),
                          proficiency=4, is_core=True, created_at=T0, updated_at=T0))
        s.add(m.JobDescription(job_id=i("job1"), user_id=uid, title="Data Scientist",
                               company="Rippling Test Co", description="Build models.",
                               status="tailored", application_status="applied",
                               created_at=T0, updated_at=T0))
        s.add(m.JobDescription(job_id=i("job2"), user_id=uid, title="ML Engineer",
                               company="Other Test Co", description="Ship models.",
                               created_at=T0, updated_at=T0))
        s.commit()
        s.add(m.JobSkill(job_skill_id=i("js"), job_id=i("job1"), skill_id=i("sk-sql"),
                         required=True, weight=2.5, created_at=T0, updated_at=T0))
        s.add(m.UserJobResult(result_id=i("res"), user_id=uid, job_id=i("job1"), seq=0,
                              ats_score=71.5, matched_skills={"Python": 1.0},
                              missing_skills=["Spark"], tailored_resume_content={"x": [1, 2]},
                              tailoring_decisions=[{"a": 1}], layout_overrides={"skills": ["SQL"]},
                              created_at=T0, updated_at=T0))
        s.add(m.ChatMessage(message_id=i("msg"), job_id=i("job1"), user_id=uid, role="user",
                            content="Lead with the forecasting work", seq=0, created_at=T0))
        s.add(m.JobCard(card_id=i("card"), user_id=uid, job_id=i("job1"), title="Data Scientist",
                        company="Rippling Test Co", role_family="data_science",
                        payload={"emphasized": ["exp:data intern|acme analytics"]},
                        index_keys=["python"], created_at=T0, updated_at=T0))
        s.add(m.JDProfile(profile_id=i("jdp"), job_id=i("job1"), user_id=uid,
                          payload={"requirements": [{"text": "SQL"}]}, weights={"sql": 1.0},
                          eligibility={str(i("rule")): {"answer": True, "source": "host"}},
                          created_at=T0, updated_at=T0))
        s.add(m.UserPreference(preference_id=i("pref1"), user_id=uid,
                               text="Never mention coursework projects", polarity="suppress",
                               target_key="proj:coursework", scope_type="global", strength=5,
                               provenance={"quote": "never"}, created_at=T0, updated_at=T0))
        s.add(m.UserPreference(preference_id=i("pref2"), user_id=uid,
                               text="Lead with forecasting", polarity="emphasize",
                               scope_type="role_family", scope_value="data_science",
                               supersedes_id=i("pref1"), created_at=T0, updated_at=T0))
        s.add(m.PersonaTrait(trait_id=i("trait"), user_id=uid, group_key="suppress|topic|global",
                             label="Avoids coursework", leaf_ids=[str(i("pref1"))],
                             created_at=T0, updated_at=T0))
        s.add(m.Persona(persona_id=i("persona"), user_id=uid, compiled={"traits": []},
                        compiled_hash="h", created_at=T0, updated_at=T0))
        s.add(m.DeletedEntry(id=7 + n0, user_id=uid, entity_type="project", key_a="Old",
                             key_b=None, created_at=T0))
        s.add(m.TailorNode(node_id=i("n1"), user_id=uid, job_id=i("job1"), parent_id=None,
                           seq=0, source="host", content={"bullets": ["a"]},
                           provenance={"host": "claude-code", "art_version": "0.1.0"},
                           created_at=T0))
        s.add(m.TailorNode(node_id=i("n2"), user_id=uid, job_id=i("job1"), parent_id=i("n1"),
                           seq=1, source="editor", content={"bullets": ["b"]},
                           program={"ops": []}, metrics={"coverage": 0.5},
                           edited_tex="\\documentclass{article}", result_id=i("res"),
                           note="tweak", created_at=T0))
        s.add(m.JobHead(job_id=i("job1"), user_id=uid, node_id=i("n2"), updated_at=T0))
        s.add(m.TreeEvent(event_id=3 + n0, user_id=uid, job_id=i("job1"), node_id=i("n2"),
                          kind="commit", created_at=T0))
        s.add(m.TreeEvent(event_id=9 + n0, user_id=uid, job_id=i("job1"), node_id=i("n1"),
                          kind="checkout", created_at=T0))
        s.add(m.PlanProgram(program_id="abc123" + tag, user_id=uid, job_id=i("job1"),
                            program={"ops": [{"op": "keep"}]}, created_at=T0))
        s.add(m.JobRule(rule_id=i("rule"), user_id=uid, item_key="edu:example university|m.s.",
                        field="end_date", question="Must you be enrolled after the internship?",
                        value_if_yes="Dec 2027", value_if_no="June 2027",
                        created_at=T0, updated_at=T0))
        s.add(m.BulletVariant(variant_id=i("var"), user_id=uid, item_key="exp:data intern|acme",
                              text="Built a churn model that flagged 12% of accounts",
                              tags={"track": "data_science"}, status="approved",
                              cites=["exp:data intern|acme#b0"], line_count=2,
                              source_node_id=i("n1"), created_at=T0, updated_at=T0))
        s.add(m.TrackBaseline(user_id=uid, track="data_science", node_id=i("n2"),
                              job_id=i("job1"), saved_at=T0))
        s.add(m.JobRoleFamily(job_id=i("job1"), user_id=uid, role_family="data_science",
                              source="host", updated_at=T0))
        s.add(m.JevDecision(cache_key=("k" + tag) * 8, point="variant_choice", question_version="v1",
                            requested_model="m", resolved_model="m1", question={"q": 1},
                            answer={"a": {"x": 0.5}}, input_tokens=10.0, output_tokens=2.0,
                            created_at=T0))
        s.add(m.BlockLineCache(text_hash="t" + tag, template_hash="p" + tag, lines=2,
                               engine="tectonic 0.15", measured_at=T0))
        s.commit()
    return uid


def seed_applications(root: Path):
    d = root / "Rippling_Test_Co_Data_Scientist"
    d.mkdir(parents=True)
    (d / "resume.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
    (d / "resume.pdf").write_bytes(b"%PDF-1.4\n" + bytes(range(256)))
    (root / "notes.txt").write_text("keep me", encoding="utf-8")


def _zip_entries(path: Path):
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i.filename) for i in zf.infolist()}


def _normalized_manifest(entries):
    doc = json.loads(entries["manifest.json"])
    doc["exported_at"] = "<t>"
    return doc


def _export(engine, tmp_path, name, uid=UID, **kw):
    out = tmp_path / f"{name}.zip"
    apps = kw.pop("apps_dir", tmp_path / "apps_a")
    ei.export_bundle(engine, uid, out, apps_dir=apps, **kw)
    return out


def _import(engine, path, tmp_path, **kw):
    bundle = ei.read_bundle(path)
    try:
        return ei.import_bundle(engine, bundle, apps_dir=kw.pop("apps_dir", tmp_path / "apps_b"),
                                **kw)
    finally:
        bundle.close()


def _count(engine, model, **where):
    with Session(engine) as s:
        q = sa.select(sa.func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return s.execute(q).scalar()


# ── the registry ─────────────────────────────────────────────────────────────

def test_every_table_is_exported_or_declared_not_exported():
    """A new table has to be decided on: exported, or named here with a reason."""
    tables = set(SQLModel.metadata.tables)
    covered = {s.name for s in ei.specs()}
    assert tables == covered | set(ei.NOT_EXPORTED)
    assert not covered & set(ei.NOT_EXPORTED)


def test_the_registry_is_in_foreign_key_order():
    order = [s.name for s in ei.specs()]
    for spec in ei.specs():
        for _col, parent, _ in spec.foreign_keys():
            assert order.index(parent) < order.index(spec.name), (spec.name, parent)
        if spec.via:
            assert order.index(spec.via[1]) < order.index(spec.name)


def test_export_and_import_are_cli_commands_not_mcp_tools():
    from harness.contract import BY_NAME
    assert not {"export", "import", "art_export", "art_import"} & set(BY_NAME)


# ── the round trip: the acceptance criterion ─────────────────────────────────

def test_export_import_export_is_identical(isolated_engine, tmp_path):
    seed(isolated_engine)
    seed_applications(tmp_path / "apps_a")

    first = _export(isolated_engine, tmp_path, "first", include_cache=True)
    dest = _sqlite(tmp_path)
    report = _import(dest, first, tmp_path)
    assert report["user_id"] == str(UID) and report["files"]["written"] == 3

    second = _export(dest, tmp_path, "second", include_cache=True,
                     apps_dir=tmp_path / "apps_b")
    a, b = _zip_entries(first), _zip_entries(second)
    assert _normalized_manifest(a) == _normalized_manifest(b)
    assert a.keys() == b.keys()
    for name in a:
        if name != "manifest.json":
            assert a[name] == b[name], name

    counts = json.loads(a["manifest.json"])["counts"]
    empty = {t for t, n in counts.items() if n == 0}
    assert not empty, f"the fixture must touch every table; empty: {empty}"
    assert set(counts) == {s.name for s in ei.specs()}


def test_the_round_trip_keeps_keys_links_and_values(isolated_engine, tmp_path):
    seed(isolated_engine)
    dest = _sqlite(tmp_path)
    _import(dest, _export(isolated_engine, tmp_path, "b"), tmp_path)
    with Session(dest) as s:
        n2 = s.get(m.TailorNode, _id("n2"))
        assert n2.parent_id == _id("n1") and n2.edited_tex.startswith("\\documentclass")
        assert s.get(m.JobHead, _id("job1")).node_id == _id("n2")
        base = s.get(m.TrackBaseline, (UID, "data_science"))
        assert base.node_id == _id("n2") and base.job_id == _id("job1")
        var = s.get(m.BulletVariant, _id("var"))
        assert var.cites == ["exp:data intern|acme#b0"] and var.status == "approved"
        assert s.get(m.JobRoleFamily, _id("job1")).source == "host"
        assert s.get(m.Experience, _id("exp")).bullets[0] == "Built a churn model"
        assert s.get(m.Project, _id("proj")).experience_id == _id("exp")
        pins = [p for p in s.execute(sa.select(m.UserPreference)).scalars()
                if p.strength == 5 and p.polarity == "suppress"]
        assert len(pins) == 1
        assert s.get(m.UserSkill, _id("us")).is_core is True
        assert s.get(m.JDProfile, _id("jdp")).eligibility[str(_id("rule"))]["answer"] is True
        assert s.get(m.DeletedEntry, 7).key_a == "Old"
        assert s.get(m.TreeEvent, 9).kind == "checkout"
        user = s.get(m.User, UID)
        assert user.onboarding_steps == {"resume": True} and user.created_at == T0


def test_two_exports_of_one_store_differ_only_in_the_manifest(isolated_engine, tmp_path):
    seed(isolated_engine)
    a = _zip_entries(_export(isolated_engine, tmp_path, "one"))
    b = _zip_entries(_export(isolated_engine, tmp_path, "two"))
    assert {k: v for k, v in a.items() if k != "manifest.json"} == \
           {k: v for k, v in b.items() if k != "manifest.json"}
    assert "tables/jevdecision.json" not in a  # caches only with --include-cache


def test_the_manifest_names_the_format_the_user_and_the_counts(isolated_engine, tmp_path):
    from harness import ART_VERSION
    seed(isolated_engine)
    doc = json.loads(_zip_entries(_export(isolated_engine, tmp_path, "m"))["manifest.json"])
    assert doc["format"] == "art-export" and doc["format_version"] == 1
    assert doc["art_version"] == ART_VERSION and doc["user_id"] == str(UID)
    assert doc["exported_at"].endswith("Z") and datetime.strptime(
        doc["exported_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert doc["counts"]["experience"] == 1 and doc["counts"]["treeevent"] == 2
    assert doc["include_cache"] is False


# ── the destination: refuse, merge, replace ──────────────────────────────────

@pytest.fixture()
def bundle_path(isolated_engine, tmp_path):
    seed(isolated_engine)
    seed_applications(tmp_path / "apps_a")
    return _export(isolated_engine, tmp_path, "bundle")


def test_a_second_import_is_refused_without_merge_or_replace(bundle_path, tmp_path):
    dest = _sqlite(tmp_path)
    _import(dest, bundle_path, tmp_path)
    with pytest.raises(PortError) as exc:
        _import(dest, bundle_path, tmp_path)
    assert exc.value.code == "destination_not_empty"
    assert "experience: 1" in exc.value.message


def test_merge_adds_what_is_missing_and_never_overwrites(bundle_path, tmp_path):
    dest = _sqlite(tmp_path)
    _import(dest, bundle_path, tmp_path)
    with Session(dest) as s:
        exp = s.get(m.Experience, _id("exp"))
        exp.title = "Edited Locally"
        s.add(exp)
        s.delete(s.get(m.JobRoleFamily, _id("job1")))
        s.add(m.Experience(experience_id=uuid4(), user_id=UID, title="Local only", company="X"))
        s.commit()
    report = _import(dest, bundle_path, tmp_path, mode="merge")
    assert report["imported"] == {"jobrolefamily": 1}
    assert report["skipped_existing"]["experience"] == 1
    with Session(dest) as s:
        assert s.get(m.Experience, _id("exp")).title == "Edited Locally"
    assert _count(dest, m.Experience) == 2
    # events and deleted-entry tombstones are matched on their content, not their ids
    assert _count(dest, m.TreeEvent) == 2 and _count(dest, m.DeletedEntry) == 1


def test_replace_needs_confirmation_then_deletes_what_was_there(bundle_path, tmp_path):
    dest = _sqlite(tmp_path)
    _import(dest, bundle_path, tmp_path)
    extra = uuid4()
    with Session(dest) as s:
        s.add(m.Experience(experience_id=extra, user_id=UID, title="Stale", company="Gone"))
        s.commit()
    with pytest.raises(PortError) as exc:
        _import(dest, bundle_path, tmp_path, mode="replace")
    assert exc.value.code == "confirm_required" and "experience: 2" in exc.value.message
    assert _count(dest, m.Experience) == 2  # nothing was deleted

    report = _import(dest, bundle_path, tmp_path, mode="replace", confirm_replace=True)
    assert report["imported"]["experience"] == 1
    assert _count(dest, m.Experience) == 1 and _count(dest, m.TailorNode) == 2


def test_a_dry_run_reports_and_changes_nothing(bundle_path, tmp_path):
    dest = _sqlite(tmp_path)
    report = _import(dest, bundle_path, tmp_path, dry_run=True)
    assert report["dry_run"] is True and report["imported"]["experience"] == 1
    assert _count(dest, m.User) == 0 and _count(dest, m.Experience) == 0
    assert not (tmp_path / "apps_b").exists()


def test_importing_one_bundle_under_two_user_ids_is_a_conflict(bundle_path, tmp_path):
    dest = _sqlite(tmp_path)
    _import(dest, bundle_path, tmp_path)
    with Session(dest) as s:  # so the email is not what stops it
        user = s.get(m.User, UID)
        user.email = "elsewhere@example.org"
        s.add(user)
        s.commit()
    other = uuid4()
    with pytest.raises(PortError) as exc:
        _import(dest, bundle_path, tmp_path, user_id=other)
    assert exc.value.code == "pk_conflict"
    assert _count(dest, m.Experience, user_id=other) == 0  # nothing was written


def test_an_incoming_skill_lands_on_the_one_already_stored_by_name(bundle_path, tmp_path):
    dest = _sqlite(tmp_path)
    local_python = uuid4()
    with Session(dest) as s:
        s.add(m.Skill(skill_id=local_python, name=" python "))
        s.commit()
    _import(dest, bundle_path, tmp_path)
    with Session(dest) as s:
        assert s.get(m.UserSkill, _id("us")).skill_id == local_python
        names = [n.lower().strip() for n in s.execute(sa.select(m.Skill.name)).scalars()]
    assert names.count("python") == 1 and "sql" in names


# ── which profile an import binds ────────────────────────────────────────────

@pytest.mark.parametrize("email", ["user@example.com", "someone@local", "USER@EXAMPLE.COM"])
def test_a_local_fallback_profile_is_refused(isolated_engine, tmp_path, email):
    seed(isolated_engine, email=email)
    path = _export(isolated_engine, tmp_path, "fb")
    dest = _sqlite(tmp_path)
    with pytest.raises(PortError) as exc:
        _import(dest, path, tmp_path)
    assert exc.value.code == "fallback_profile" and _count(dest, m.User) == 0
    # ...unless the override is passed
    _import(dest, path, tmp_path, allow_fallback=True)
    assert _count(dest, m.Experience) == 1


def test_a_fallback_profile_already_in_the_destination_is_refused(bundle_path, tmp_path):
    """The stale profile `get_or_create_cli_user` makes must not receive a real one's data."""
    from database.user_utils import CLI_DEFAULT_EMAIL, CLI_DEFAULT_NAME
    dest = _sqlite(tmp_path)
    with Session(dest) as s:
        s.add(m.User(user_id=UID, name=CLI_DEFAULT_NAME, email=CLI_DEFAULT_EMAIL))
        s.commit()
    with pytest.raises(PortError) as exc:
        _import(dest, bundle_path, tmp_path)
    assert exc.value.code == "fallback_profile"
    assert _count(dest, m.Experience) == 0


def test_the_bundles_user_is_bound_unless_user_id_overrides_it(bundle_path, tmp_path):
    other = uuid4()
    dest = _sqlite(tmp_path)
    report = _import(dest, bundle_path, tmp_path, user_id=other)
    assert report["user_id"] == str(other)
    assert any("imported as" in w for w in report["warnings"])
    assert _count(dest, m.Experience, user_id=other) == 1 and _count(dest, m.User) == 1
    assert _count(dest, m.User, user_id=UID) == 0
    dest2 = _sqlite(tmp_path, "dest2")
    assert _import(dest2, bundle_path, tmp_path)["user_id"] == str(UID)


def test_another_profile_with_the_same_email_is_a_conflict(bundle_path, tmp_path):
    dest = _sqlite(tmp_path)
    with Session(dest) as s:
        s.add(m.User(user_id=uuid4(), name="Someone", email="ada@example.org"))
        s.commit()
    with pytest.raises(PortError) as exc:
        _import(dest, bundle_path, tmp_path)
    assert exc.value.code == "email_conflict"


# ── no secrets ───────────────────────────────────────────────────────────────

def test_no_key_or_secret_is_written(isolated_engine, tmp_path):
    seed(isolated_engine)
    with Session(isolated_engine) as s:
        s.add(m.ChatMessage(message_id=uuid4(), job_id=_id("job1"), user_id=UID, role="user",
                            content=f"use {FAKE_API_KEY} and {FAKE_GH_TOKEN} please", seq=1,
                            created_at=T0))
        s.add(m.UserPreference(preference_id=uuid4(), user_id=UID, text="x",
                               provenance={"note": f"conn {FAKE_DB_URL}"},
                               created_at=T0, updated_at=T0))
        s.commit()
    out = _export(isolated_engine, tmp_path, "secrets", include_cache=True)
    entries = _zip_entries(out)
    blob = b"\n".join(entries.values())
    for secret in (FAKE_PASSWORD_HASH, FAKE_GH_TOKEN, FAKE_API_KEY, "hunter2pass", "sb-00000000"):
        assert secret.encode() not in blob, secret

    user_cols = set(json.loads(entries["tables/user.json"])["rows"][0])
    assert not user_cols & set(ei.USER_SECRET_COLUMNS)
    assert not any(any(p in c for p in ("password", "secret", "api_key", "access_token"))
                   for name, raw in entries.items() if name.startswith("tables/")
                   for row in json.loads(raw)["rows"] for c in row)
    manifest = json.loads(entries["manifest.json"])
    assert manifest["redacted"] == 3
    redacted_chat = [r for r in json.loads(entries["tables/chatmessage.json"])["rows"]
                     if "[REDACTED]" in r["content"]]
    assert len(redacted_chat) == 1


def test_an_import_never_takes_a_credential_from_a_bundle(bundle_path, tmp_path):
    """A bundle that carries one anyway is read with those columns dropped."""
    forged = tmp_path / "forged.zip"
    entries = _zip_entries(bundle_path)
    doc = json.loads(entries["tables/user.json"])
    doc["rows"][0].update(password_hash="x", github_access_token="y", supabase_uid="z")
    entries["tables/user.json"] = json.dumps(doc).encode()
    with zipfile.ZipFile(forged, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    dest = _sqlite(tmp_path)
    report = _import(dest, forged, tmp_path)
    assert any("unknown column" in w and "password_hash" in w for w in report["warnings"])
    with Session(dest) as s:
        user = s.get(m.User, UID)
        assert (user.password_hash, user.github_access_token, user.supabase_uid) == (None,) * 3


# ── older bundles ────────────────────────────────────────────────────────────

def _bundle_dir(tmp_path, tables, manifest=None, files=None):
    root = tmp_path / "old_bundle"
    (root / "tables").mkdir(parents=True)
    manifest = manifest or {"format": "art-export", "format_version": 1, "art_version": "0.0.9",
                            "exported_at": "2026-01-01T00:00:00Z", "user_id": str(UID)}
    (root / "manifest.json").write_text(json.dumps(manifest))
    for name, rows in tables.items():
        (root / "tables" / f"{name}.json").write_text(json.dumps({"table": name, "rows": rows}))
    for rel, data in (files or {}).items():
        p = root / "applications" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def test_a_bundle_from_an_older_schema_imports_with_defaults_and_warnings(tmp_path):
    job, proj = str(_id("job1")), str(_id("proj"))
    root = _bundle_dir(tmp_path, {
        "user": [{"user_id": str(UID), "name": "Ada Example", "email": "ada@example.org",
                  "legacy_flag": True, "password_hash": "nope"}],
        "experience": [{"experience_id": str(_id("exp")), "user_id": str(UID),
                        "title": "Data Intern", "company": "Acme", "bullets": ["a"]},
                       {"experience_id": str(_id("exp2")), "user_id": str(UID),
                        "company": "No Title Inc"}],
        "achievement": [{"achievement_id": str(_id("ach")), "user_id": str(UID),
                         "title": "Prize", "project_id": proj}],
        "jobdescription": [{"job_id": job, "user_id": str(UID), "title": "DS", "company": "Co",
                            "description": "d"}],
        "userjobresult": [{"result_id": str(_id("res")), "user_id": str(UID), "job_id": job,
                           "ats_score": 50},
                          {"result_id": str(_id("res2")), "user_id": str(UID),
                           "job_id": str(_id("gone"))}],
        "widgets": [{"id": 1}],
    })
    dest = _sqlite(tmp_path)
    report = _import(dest, root, tmp_path)
    warnings = "\n".join(report["warnings"])
    assert "ignored table widgets" in warnings
    assert "dropped unknown column(s) legacy_flag" in warnings
    assert "missing required title" in warnings
    assert "pointed at a missing project; cleared" in warnings
    assert report["dropped"] == {"userjobresult": 1}
    with Session(dest) as s:
        exp = s.get(m.Experience, _id("exp"))
        assert (exp.manually_edited, exp.seq, exp.source_context) == (False, None, None)
        assert s.get(m.Achievement, _id("ach")).project_id is None
        assert s.get(m.JobDescription, _id("job1")).application_status == "drafting"
        assert s.get(m.UserJobResult, _id("res")).tailoring_decisions == []
        assert s.get(m.User, UID).password_hash is None
    assert _count(dest, m.Experience) == 1


def test_a_bundle_with_no_user_row_gets_a_placeholder_profile(tmp_path):
    root = _bundle_dir(tmp_path, {"skill": [{"skill_id": str(_id("sk-py")), "name": "Python"}]})
    dest = _sqlite(tmp_path)
    report = _import(dest, root, tmp_path)
    assert any("placeholder" in w for w in report["warnings"])
    assert _count(dest, m.User, user_id=UID) == 1


@pytest.mark.parametrize("manifest,why", [
    ({"format": "something-else", "format_version": 1}, "not an ART export"),
    ({"format": "art-export", "format_version": 99}, "newer ART"),
    ({"format": "art-export"}, "bad format_version"),
])
def test_a_bundle_that_is_not_understood_is_refused(tmp_path, manifest, why):
    root = _bundle_dir(tmp_path, {}, manifest=manifest)
    with pytest.raises(PortError) as exc:
        ei.read_bundle(root)
    assert exc.value.code == "invalid_bundle" and why in exc.value.message


def test_things_that_are_not_bundles_are_refused(tmp_path):
    for bad in (tmp_path / "missing.zip",):
        with pytest.raises(PortError):
            ei.read_bundle(bad)
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip")
    with pytest.raises(PortError):
        ei.read_bundle(junk)


def test_an_unzipped_bundle_directory_imports_too(bundle_path, tmp_path):
    unzipped = tmp_path / "unzipped"
    with zipfile.ZipFile(bundle_path) as zf:
        zf.extractall(unzipped)
    dest = _sqlite(tmp_path)
    report = _import(dest, unzipped, tmp_path)
    assert report["imported"]["experience"] == 1 and report["files"]["written"] == 3


# ── applications/ ────────────────────────────────────────────────────────────

def test_applications_round_trip_byte_for_byte(bundle_path, tmp_path):
    dest = _sqlite(tmp_path)
    _import(dest, bundle_path, tmp_path)
    src, got = tmp_path / "apps_a", tmp_path / "apps_b"
    for f in src.rglob("*"):
        if f.is_file():
            assert (got / f.relative_to(src)).read_bytes() == f.read_bytes()


def test_restoring_keeps_a_different_file_unless_replacing(bundle_path, tmp_path):
    apps = tmp_path / "apps_b" / "Rippling_Test_Co_Data_Scientist"
    apps.mkdir(parents=True)
    (apps / "resume.tex").write_text("edited since the backup")
    dest = _sqlite(tmp_path)
    report = _import(dest, bundle_path, tmp_path)
    assert report["files"] == {"written": 2, "unchanged": 0, "skipped": 1}
    assert (apps / "resume.tex").read_text() == "edited since the backup"
    report = _import(dest, bundle_path, tmp_path, mode="replace", confirm_replace=True)
    assert (apps / "resume.tex").read_text() == "\\documentclass{article}\n"
    assert report["files"]["written"] == 1 and report["files"]["unchanged"] == 2


def test_a_path_that_climbs_out_of_applications_is_not_written(tmp_path):
    zpath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "format": "art-export", "format_version": 1, "user_id": str(UID)}))
        zf.writestr("applications/../escaped.txt", "x")
        zf.writestr("applications/ok/resume.tex", "fine")
        zf.writestr("applications/C:/win.txt", "x")
    dest = _sqlite(tmp_path)
    report = _import(dest, zpath, tmp_path)
    assert report["files"]["written"] == 1
    assert (tmp_path / "apps_b" / "ok" / "resume.tex").read_text() == "fine"
    assert not (tmp_path / "escaped.txt").exists()
    assert sum("unsafe path" in w for w in report["warnings"]) == 2


# ── an older source schema, on any engine ────────────────────────────────────

def _old_source(tmp_path):
    """A web-app database from before most of the schema: bare tables, TEXT booleans and
    JSON, a job with no owner that a result points at, and no tree or library tables."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old_source.db'}")
    uid, job, orphan = UID.hex, _id("job1").hex, _id("orphan-job").hex
    with engine.begin() as c:
        for ddl in (
            'CREATE TABLE "user" (user_id CHAR(32) PRIMARY KEY, name TEXT, email TEXT, '
            "onboarding_complete INTEGER DEFAULT 0, password_hash TEXT, created_at DATETIME)",
            "CREATE TABLE experience (experience_id CHAR(32) PRIMARY KEY, user_id CHAR(32), "
            "title TEXT, company TEXT, bullets TEXT, created_at DATETIME)",
            "CREATE TABLE jobdescription (job_id CHAR(32) PRIMARY KEY, user_id CHAR(32), "
            "title TEXT, company TEXT, description TEXT)",
            "CREATE TABLE userjobresult (result_id CHAR(32) PRIMARY KEY, user_id CHAR(32), "
            "job_id CHAR(32), ats_score FLOAT, tailored_resume_content TEXT, "
            "tailoring_decisions TEXT, created_at DATETIME)",
            "CREATE TABLE userpreference (preference_id CHAR(32) PRIMARY KEY, user_id CHAR(32), "
            "text TEXT, polarity TEXT, strength INTEGER)",
        ):
            c.execute(sa.text(ddl))
        c.execute(sa.text('INSERT INTO "user" VALUES (:u, \'Ada Example\', \'ada@example.org\', '
                          "1, 'secret-hash', '2025-01-02 03:04:05.000000')"), {"u": uid})
        c.execute(sa.text("INSERT INTO experience VALUES (:e, :u, 'Data Intern', 'Acme', "
                          '\'["one", "two"]\', \'2025-02-03 04:05:06.000000\')'),
                  {"e": _id("exp").hex, "u": uid})
        c.execute(sa.text("INSERT INTO jobdescription VALUES (:j, :u, 'DS', 'Co', 'd')"),
                  {"j": job, "u": uid})
        c.execute(sa.text("INSERT INTO jobdescription VALUES (:j, NULL, 'Old', 'Ownerless', 'd')"),
                  {"j": orphan})
        c.execute(sa.text("INSERT INTO jobdescription VALUES (:j, :o, 'Theirs', 'Other', 'd')"),
                  {"j": _id("theirs").hex, "o": uuid4().hex})
        for rid, jid in (("r1", job), ("r2", orphan)):
            c.execute(sa.text(
                "INSERT INTO userjobresult VALUES (:r, :u, :j, 60.5, '{\"k\": [1]}', '[]', "
                "'2025-03-04 05:06:07.000000')"), {"r": _id(rid).hex, "u": uid, "j": jid})
        c.execute(sa.text("INSERT INTO userpreference VALUES (:p, :u, 'No fluff', 'suppress', 5)"),
                  {"p": _id("p").hex, "u": uid})
        # another profile's data, which must never come across
        c.execute(sa.text("INSERT INTO experience VALUES (:e, :u, 'Theirs', 'Other', '[]', NULL)"),
                  {"e": uuid4().hex, "u": uuid4().hex})
    return engine


def test_reading_an_older_source_tolerates_missing_tables_and_columns(tmp_path):
    source = _old_source(tmp_path)
    bundle = ei.read_source(source, UID)
    w = "\n".join(bundle.warnings)
    assert "table tailornode is not in the source" in w
    assert "table experience: source has no column(s)" in w and "seq" in w
    assert "1 job(s) with no owner are used by this profile" in w
    assert [r["title"] for r in bundle.tables["experience"]] == ["Data Intern"]
    assert {r["title"] for r in bundle.tables["jobdescription"]} == {"DS", "Old"}
    assert "password_hash" not in bundle.tables["user"][0]
    assert bundle.manifest["source"] == "supabase" and bundle.manifest["counts"]["userjobresult"] == 2

    dest = _sqlite(tmp_path)
    report = ei.import_bundle(dest, bundle, user_id=UID, apps_dir=tmp_path / "apps")
    assert report["imported"]["userjobresult"] == 2 and report["dropped"] == {}
    with Session(dest) as s:
        user = s.get(m.User, UID)
        assert user.onboarding_complete is True and user.password_hash is None
        assert user.created_at == datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        exp = s.execute(sa.select(m.Experience)).scalars().one()
        assert exp.bullets == ["one", "two"] and exp.seq is None
        res = s.get(m.UserJobResult, _id("r1"))
        assert res.tailored_resume_content == {"k": [1]} and res.tailoring_decisions == []
        orphan = s.execute(sa.select(m.JobDescription).where(
            m.JobDescription.company == "Ownerless")).scalars().one()
        assert orphan.user_id == UID
    assert _count(dest, m.Experience) == 1


def test_a_source_that_does_not_know_the_user_says_so(tmp_path):
    with pytest.raises(PortError) as exc:
        ei.read_source(_old_source(tmp_path), uuid4())
    assert exc.value.code == "no_such_user"


def test_a_source_with_the_user_but_no_data_is_an_error_not_an_empty_import(tmp_path):
    source = _old_source(tmp_path)
    with source.begin() as c:
        for table in ("experience", "jobdescription", "userjobresult", "userpreference"):
            c.execute(sa.text(f"DELETE FROM {table}"))
    with pytest.raises(PortError) as exc:
        ei.read_source(source, UID)
    assert exc.value.code == "no_user_data"


def test_the_source_connection_cannot_write(tmp_path):
    source = _old_source(tmp_path)
    before = _row_dump(source)
    with ei.read_only(source) as conn:
        with pytest.raises(sa.exc.OperationalError):
            conn.execute(sa.text("DELETE FROM experience"))
        with pytest.raises(sa.exc.OperationalError):
            conn.execute(sa.text("CREATE TABLE sneaky (a INTEGER)"))
    assert _row_dump(source) == before
    with source.begin() as c:  # and the pooled connection is writable again afterwards
        c.execute(sa.text("DELETE FROM userpreference"))


def _row_dump(engine):
    with engine.connect() as c:
        names = sorted(r[0] for r in c.execute(sa.text(
            "SELECT name FROM sqlite_master WHERE type = 'table'")))
        return {n: sorted(map(tuple, c.execute(sa.text(f'SELECT * FROM "{n}"')).all()))
                for n in names}


# ── the commands ─────────────────────────────────────────────────────────────

def _args(*argv):
    return ei._parser().parse_args(list(argv))


def _run(capsys, engine, *argv):
    code = ei.run(_args(*argv), engine=engine)
    return code, json.loads(capsys.readouterr().out)


def test_from_supabase_needs_an_explicit_user_id_and_source(tmp_path, capsys, monkeypatch):
    dest = _sqlite(tmp_path)
    code, out = _run(capsys, dest, "import", "--from-supabase", "--source-url",
                     "postgresql://u:p@host.invalid/db")
    assert code == 2 and out["error"]["code"] == "user_id_required"

    # DATABASE_URL is never the source
    monkeypatch.setenv("DATABASE_URL", "postgresql://prod:prodpass@prod.invalid/prod")
    monkeypatch.delenv("ART_IMPORT_SOURCE_URL", raising=False)
    code, out = _run(capsys, dest, "import", "--from-supabase", "--user-id", str(UID))
    assert code == 2 and out["error"]["code"] == "source_required"
    assert "prodpass" not in json.dumps(out)


def test_the_source_url_can_come_from_its_own_env_var(tmp_path, capsys, monkeypatch):
    seen = []
    monkeypatch.setattr(ei, "open_source", lambda url: seen.append(url) or _old_source(tmp_path))
    monkeypatch.setenv("ART_IMPORT_SOURCE_URL", "postgresql://u:from-env@host.invalid/db")
    dest = _sqlite(tmp_path)
    code, out = _run(capsys, dest, "import", "--from-supabase", "--user-id", str(UID))
    assert code == 0 and seen == ["postgresql://u:from-env@host.invalid/db"]
    assert out["source"] == "supabase" and out["imported"]["experience"] == 1


def test_bad_argument_combinations_are_refused(tmp_path, capsys):
    dest = _sqlite(tmp_path)
    for argv, code_ in (
        (("import",), "invalid_arguments"),
        (("import", "x.zip", "--source-url", "postgresql://u@h/d"), "invalid_arguments"),
        (("import", "x.zip", "--from-supabase", "--user-id", str(UID)), "invalid_arguments"),
        (("import", "x.zip", "--user-id", "not-a-uuid"), "invalid_arguments"),
        (("import", "--from-supabase", "--user-id", str(UID), "--source-url",
          "sqlite:///x.db"), "invalid_source"),
        (("import", str(tmp_path / "nope.zip")), "invalid_bundle"),
    ):
        code, out = _run(capsys, dest, *argv)
        assert code == 2 and out["error"]["code"] == code_, argv


def test_merge_and_replace_cannot_be_combined():
    with pytest.raises(SystemExit):
        _args("import", "x.zip", "--merge", "--replace")


def test_a_source_url_is_never_echoed_with_its_password():
    assert "s3cret" not in ei.mask_url("postgresql://svc:s3cret@db.example.test/prod")
    msg = ei._scrub("could not connect: password s3cret for postgresql://svc:s3cret@h/d",
                    "postgresql://svc:s3cret@h/d")
    assert "s3cret" not in msg


def test_export_and_import_through_run(isolated_engine, tmp_path, capsys):
    seed(isolated_engine)
    out = tmp_path / "via_run.zip"
    code, doc = _run(capsys, isolated_engine, "export", "--out", str(out), "--user-id", str(UID))
    assert code == 0 and doc["counts"]["experience"] == 1 and out.exists()
    code, doc = _run(capsys, isolated_engine, "export", "--out", str(out), "--user-id", str(UID))
    assert code == 2 and doc["error"]["code"] == "exists"
    code, doc = _run(capsys, isolated_engine, "export", "--out", str(out), "--user-id",
                     str(UID), "--force")
    assert code == 0

    dest = _sqlite(tmp_path)
    code, doc = _run(capsys, dest, "import", str(out))
    assert code == 0 and doc["source"] == "bundle" and doc["imported"]["experience"] == 1
    assert doc["active_profile"]["set"] is False and doc["active_profile"]["matches"] is False
    code, doc = _run(capsys, dest, "import", str(out))
    assert code == 2 and doc["error"]["code"] == "destination_not_empty"
    code, doc = _run(capsys, dest, "import", str(out), "--replace")
    assert code == 2 and doc["error"]["code"] == "confirm_required"
    code, doc = _run(capsys, dest, "import", str(out), "--replace", "--confirm-replace",
                     "--set-active")
    assert code == 0 and doc["active_profile"] == {"set": True, "user_id": str(UID)}
    import database.user_utils as uu
    assert uu.ACTIVE_PROFILE_FILE.read_text() == str(UID)


def test_export_names_a_directory_target_and_warns_about_a_fallback_active_profile(
        isolated_engine, tmp_path, capsys):
    import database.user_utils as uu
    seed(isolated_engine, email="user@example.com")
    uu.ACTIVE_PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    uu.ACTIVE_PROFILE_FILE.write_text(str(UID))
    target = tmp_path / "backups"
    target.mkdir()
    code, doc = _run(capsys, isolated_engine, "export", "--out", str(target))
    assert code == 0 and Path(doc["path"]).parent == target
    assert Path(doc["path"]).name.startswith(f"art-export-{str(UID)[:8]}-")
    assert any("local fallback" in w for w in doc["warnings"])


def test_export_without_a_profile_asks_for_one(isolated_engine, capsys):
    code, doc = _run(capsys, isolated_engine, "export")
    assert code == 2 and doc["error"]["code"] == "no_user"


def test_the_fallback_check():
    from database.user_utils import is_fallback_profile
    assert is_fallback_profile("user@example.com") and is_fallback_profile("a@local")
    assert is_fallback_profile(" User@Example.com ")
    assert not is_fallback_profile("ada@example.org") and not is_fallback_profile(None)


# ── end to end through the `art` entry point ─────────────────────────────────

def _art(args, data_dir, **env):
    full = {k: v for k, v in os.environ.items()
            if k not in ("DATABASE_URL", "ART_MCP_DATABASE_URL", "ART_MCP_USER_ID",
                         "ART_IMPORT_SOURCE_URL")}
    # A database the process must never touch: if anything read it, the run would fail.
    full["DATABASE_URL"] = "postgresql://poison:poison@127.0.0.1:1/never"
    full["ART_DATA_DIR"] = str(data_dir)
    full.update(env)
    p = subprocess.run([sys.executable, "-m", "harness.entry", *args], cwd=ROOT, env=full,
                       capture_output=True, text=True, timeout=180)
    return p.returncode, (json.loads(p.stdout) if p.stdout.strip() else {}), p.stderr


def test_the_art_command_backs_up_and_restores_a_store(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    seed(_sqlite_at(a))
    seed_applications(a / "applications")
    out = tmp_path / "backup.zip"

    code, doc, err = _art(["export", "--user-id", str(UID), "--out", str(out)], a)
    assert code == 0, err
    assert doc["counts"]["experience"] == 1 and doc["applications"]["files"] == 3

    code, doc, err = _art(["import", str(out)], b)
    assert code == 0, err
    assert doc["imported"]["experience"] == 1 and doc["files"]["written"] == 3
    assert (b / "applications" / "notes.txt").read_text() == "keep me"
    assert (b / "art.db").is_file()

    code, doc, _ = _art(["import", str(out)], b)
    assert code == 2 and doc["error"]["code"] == "destination_not_empty"
    code, doc, _ = _art(["import", "--from-supabase", "--user-id", str(UID)], tmp_path / "c")
    assert code == 2 and doc["error"]["code"] == "source_required"

    # a second export of the restored store matches the first
    again = tmp_path / "again.zip"
    code, _, err = _art(["export", "--user-id", str(UID), "--out", str(again)], b)
    assert code == 0, err
    x, y = _zip_entries(out), _zip_entries(again)
    assert _normalized_manifest(x) == _normalized_manifest(y)
    assert {k: v for k, v in x.items() if k != "manifest.json"} == \
           {k: v for k, v in y.items() if k != "manifest.json"}


def _sqlite_at(data_dir):
    engine = create_engine(f"sqlite:///{data_dir / 'art.db'}",
                           connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


def test_import_never_writes_anywhere_but_local_sqlite(tmp_path):
    bundle = tmp_path / "x.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "format": "art-export", "format_version": 1, "user_id": str(UID)}))
    code, doc, _ = _art(["import", str(bundle)], tmp_path / "d",
                        ART_MCP_DATABASE_URL="postgresql://u:p4ssw0rd-x@db.invalid:5432/prod")
    assert code == 2 and doc["error"]["code"] == "remote_destination"
    assert "p4ssw0rd-x" not in json.dumps(doc)


def test_exporting_a_missing_store_does_not_create_one(tmp_path):
    code, doc, _ = _art(["export", "--user-id", str(UID)], tmp_path / "nothing_here")
    assert code == 2 and doc["error"]["code"] == "no_store"
    assert not (tmp_path / "nothing_here" / "art.db").exists()


def test_a_fallback_profile_in_a_bundle_is_refused_by_the_command(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    seed(_sqlite_at(a), email="seed-data@local")
    out = tmp_path / "fb.zip"
    assert _art(["export", "--user-id", str(UID), "--out", str(out)], a)[0] == 0
    code, doc, _ = _art(["import", str(out)], tmp_path / "b")
    assert code == 2 and doc["error"]["code"] == "fallback_profile"
    code, doc, _ = _art(["import", str(out), "--allow-fallback-profile"], tmp_path / "b")
    assert code == 0


# ── Postgres enforces what SQLite does not ───────────────────────────────────

@requires_postgres
def test_inserts_and_replace_satisfy_postgres_foreign_keys(isolated_engine, tmp_path):
    """SQLite never checks a foreign key. Importing into Postgres proves the table order
    (and the delete order of --replace), and that autoincrement ids stay usable."""
    src = _sqlite(tmp_path, "src")
    seed(src)
    bundle_path = _export(src, tmp_path, "pg_dest")
    _import(isolated_engine, bundle_path, tmp_path)
    assert _count(isolated_engine, m.TailorNode) == 2
    with Session(isolated_engine) as s:  # a new event must not collide with the kept ids
        s.add(m.TreeEvent(user_id=UID, job_id=_id("job1"), node_id=_id("n2"), kind="commit"))
        s.add(m.DeletedEntry(user_id=UID, entity_type="project", key_a="New"))
        s.commit()
        assert max(e for e in s.execute(sa.select(m.TreeEvent.event_id)).scalars()) > 9
    report = _import(isolated_engine, bundle_path, tmp_path, mode="replace", confirm_replace=True)
    assert report["imported"]["tailornode"] == 2 and _count(isolated_engine, m.TreeEvent) == 2
    merged = _import(isolated_engine, bundle_path, tmp_path, mode="merge")
    assert merged["imported"] == {}


# ── a Postgres source (the Postgres leg stands in for the hosted database) ───

def _pg_source():
    """A throwaway schema on ART_TEST_DATABASE_URL: the hosted web-app database."""
    admin = create_engine(ART_TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    schema = f"art_test_src_{uuid4().hex[:12]}"
    with admin.connect() as c:
        c.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(ART_TEST_DATABASE_URL,
                           connect_args={"options": f"-csearch_path={schema}"})
    SQLModel.metadata.create_all(engine)
    return engine, schema, admin


def _pg_url(schema):
    from urllib.parse import quote
    sep = "&" if "?" in ART_TEST_DATABASE_URL else "?"
    return f"{ART_TEST_DATABASE_URL}{sep}options={quote('-csearch_path=' + schema)}"


@pytest.fixture()
def pg_source(tmp_path):
    engine, schema, admin = _pg_source()
    try:
        with engine.connect() as c:
            assert c.execute(sa.text("SELECT current_schema()")).scalar().startswith("art_test_src_")
        yield engine, schema
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def _pg_snapshot(engine):
    with engine.connect() as c:
        tables = [r[0] for r in c.execute(sa.text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() ORDER BY 1"))]
        return {t: sorted(map(repr, c.execute(sa.text(f'SELECT * FROM "{t}"')).all()))
                for t in tables}


@requires_postgres
def test_a_postgres_source_migrates_into_local_sqlite(pg_source, tmp_path):
    engine, schema = pg_source
    seed(engine)
    # another profile in the same database must stay behind
    seed(engine, uid=_id("someone-else"), email="grace@example.org")  # distinct ids needed
    before = _pg_snapshot(engine)
    source = ei.open_source(_pg_url(schema))
    bundle = ei.read_source(source, UID)
    source.dispose()
    assert _pg_snapshot(engine) == before  # read-only: nothing changed

    dest = _sqlite(tmp_path)
    report = ei.import_bundle(dest, bundle, user_id=UID, apps_dir=tmp_path / "apps")
    assert report["imported"]["experience"] == 1 and report["imported"]["tailornode"] == 2
    assert _count(dest, m.User) == 1 and _count(dest, m.Experience) == 1
    with Session(dest) as s:
        assert s.get(m.User, UID).password_hash is None
        assert s.get(m.TrackBaseline, (UID, "data_science")).node_id == _id("n2")
    # export -> the same bytes a direct export of the source would give
    direct = _zip_entries(_export(engine, tmp_path, "direct"))
    local = _zip_entries(_export(dest, tmp_path, "local", apps_dir=tmp_path / "apps"))
    for name in direct:
        if name not in ("manifest.json",) and not name.startswith("tables/jevdecision") \
                and not name.startswith("tables/blocklinecache"):
            assert direct[name] == local[name], name


@requires_postgres
def test_the_postgres_source_is_opened_in_a_read_only_transaction(pg_source):
    engine, _schema = pg_source
    seed(engine)
    before = _pg_snapshot(engine)
    with ei.read_only(engine) as conn:
        assert conn.execute(sa.text("SHOW transaction_read_only")).scalar() == "on"
        for ddl in ("DELETE FROM experience", "CREATE TABLE sneaky (a int)",
                    "UPDATE \"user\" SET name = 'x'"):
            with pytest.raises(sa.exc.DBAPIError):
                conn.execute(sa.text(ddl))
            conn.rollback()
    assert _pg_snapshot(engine) == before
    with engine.begin() as c:  # the pooled connection is writable again
        c.execute(sa.text("UPDATE experience SET title = title"))


@requires_postgres
def test_a_postgres_source_that_lags_the_models_still_migrates(pg_source, tmp_path):
    """The shapes production has: a missing table, a missing column, ids and booleans
    that were added by raw ALTERs as TEXT, a result whose decisions column is TEXT."""
    engine, schema = pg_source
    seed(engine)
    with engine.begin() as c:
        c.execute(sa.text("DROP TABLE bulletvariant, trackbaseline, jobrolefamily, jobrule, "
                          "planprogram, treeevent, jobhead, tailornode"))
        c.execute(sa.text("ALTER TABLE experience DROP COLUMN seq, DROP COLUMN manually_edited"))
        c.execute(sa.text("ALTER TABLE jobdescription DROP COLUMN application_status"))
        # an id column the old ALTER left as TEXT, holding dashed text
        c.execute(sa.text("ALTER TABLE userskill DROP CONSTRAINT userskill_user_id_fkey"))
        c.execute(sa.text("ALTER TABLE userskill ALTER COLUMN user_id TYPE text "
                          "USING user_id::text"))
        c.execute(sa.text("ALTER TABLE userjobresult DROP COLUMN tailoring_decisions"))
        c.execute(sa.text("ALTER TABLE userjobresult ADD COLUMN tailoring_decisions TEXT "
                          "DEFAULT '[]'"))
    before = _pg_snapshot(engine)
    source = ei.open_source(_pg_url(schema))
    bundle = ei.read_source(source, UID)
    source.dispose()
    assert _pg_snapshot(engine) == before
    w = "\n".join(bundle.warnings)
    assert "table tailornode is not in the source" in w
    assert "table experience: source has no column(s) manually_edited, seq" in w

    dest = _sqlite(tmp_path)
    report = ei.import_bundle(dest, bundle, user_id=UID, apps_dir=tmp_path / "apps")
    assert report["imported"]["userskill"] == 1 and report["imported"]["experience"] == 1
    assert "tailornode" not in report["imported"]
    with Session(dest) as s:
        assert s.get(m.UserSkill, _id("us")).user_id == UID
        assert s.get(m.Experience, _id("exp")).manually_edited is False
        assert s.get(m.UserJobResult, _id("res")).tailoring_decisions == []
        assert s.get(m.JobDescription, _id("job1")).application_status == "drafting"


@requires_postgres
def test_the_art_command_migrates_from_a_postgres_source(pg_source, tmp_path):
    engine, schema = pg_source
    seed(engine)
    before = _pg_snapshot(engine)
    data = tmp_path / "local"
    code, doc, err = _art(["import", "--from-supabase", "--source-url", _pg_url(schema),
                           "--user-id", str(UID), "--set-active"], data)
    assert code == 0, err
    assert doc["source"] == "supabase" and doc["imported"]["experience"] == 1
    assert (data / "active_profile_id").read_text() == str(UID)
    assert _pg_snapshot(engine) == before

    # the same, with the URL in its own env var rather than on the command line
    code, doc, err = _art(["import", "--from-supabase", "--user-id", str(UID), "--dry-run"],
                          tmp_path / "local2", ART_IMPORT_SOURCE_URL=_pg_url(schema))
    assert code == 0, err
    assert doc["dry_run"] is True
    assert schema not in json.dumps(doc)

    # a fallback profile in the source is refused
    with Session(engine) as s:
        user = s.get(m.User, UID)
        user.email = "user@example.com"
        s.add(user)
        s.commit()
    code, doc, _ = _art(["import", "--from-supabase", "--source-url", _pg_url(schema),
                         "--user-id", str(UID)], tmp_path / "local3")
    assert code == 2 and doc["error"]["code"] == "fallback_profile"
