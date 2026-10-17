"""Migrating old preference rows to the anchored model (#300).

`harness/migrations.py::migrate_preferences` brings rows written before #295 to #298 over: unkeyed
topics that now name an item are re-linked, keys whose item is gone become pending, and `section:`
rows (enforced until now as a dead term) become `limit` rules. It never guesses and never deletes,
a second run changes nothing, and `--dry-run` writes nothing.

Rows are written straight to the table, as an older build left them: `record_preference` no longer
produces a `section:` row. The `kg` fixture carries the conftest preference set. Nothing reaches the
network; the `env` fixture (benchmark profile) is for the executor test.
"""

import json
from uuid import UUID

import pytest
from sqlmodel import Session, select

import services
from database.models import ResumeRule, Skill, UserPreference, UserSkill
from harness import migrations, tools
from harness.migrations import MIGRATION_ID, migrate_preferences

from test_executor import PROJ, _run, env  # noqa: F401  (fixture)


# ── fixtures and snapshots ───────────────────────────────────────────────────

def _add(engine, uid, text, polarity="suppress", key=None, term=None, scope="global", value=None,
         strength=5, ttype="topic"):
    with Session(engine) as s:
        row = UserPreference(user_id=uid, text=text, polarity=polarity, target_key=key,
                             target_term=term, target_type=ttype, scope_type=scope,
                             scope_value=value, strength=strength)
        s.add(row)
        s.commit()
        s.refresh(row)
        return str(row.preference_id)


def _skill(engine, uid, name, category="tool"):
    """A skill the user holds, written straight to the tables (no ingest, so no `relink` runs)."""
    with Session(engine) as s:
        skill = Skill(name=name, category=category)
        s.add(skill)
        s.commit()
        s.refresh(skill)
        s.add(UserSkill(user_id=uid, skill_id=skill.skill_id, proficiency=3,
                        evidence_source="resume", confidence_score=0.9))
        s.commit()


def _snapshot(engine, uid):
    """Every preference and rule row, as plain data: what "changed nothing" is compared on."""
    with Session(engine) as s:
        prefs = sorted((str(p.preference_id), p.status, p.target_key, p.target_term,
                        json.dumps(p.provenance, sort_keys=True, default=str))
                       for p in s.exec(select(UserPreference).where(UserPreference.user_id == uid)))
        rules = sorted((str(r.rule_id), r.status, r.kind, json.dumps(r.params, sort_keys=True),
                        r.strength, r.scope_type, r.scope_value)
                       for r in s.exec(select(ResumeRule).where(ResumeRule.user_id == uid)))
    return prefs, rules


def _pref(engine, pid):
    with Session(engine) as s:
        return s.get(UserPreference, UUID(pid))


def _active(engine, uid, **match):
    with Session(engine) as s:
        return [p for p in s.exec(select(UserPreference).where(UserPreference.user_id == uid))
                if p.status == "active" and all(getattr(p, k) == v for k, v in match.items())]


def _codes(report):
    return {r["preference_id"]: r["code"] for r in report["left_alone"]}


@pytest.fixture()
def legacy(kg, isolated_engine):
    """The conftest set (`kg`) plus one row of each kind a pre-#295 store holds. Returns the ids."""
    e, uid = isolated_engine, kg
    ids = {}
    _skill(e, uid, "Kafka")
    _skill(e, uid, "PyTorch", "framework")
    ids["section_projects"] = _add(e, uid, "Never show the projects section", key="section:projects",
                                   term="section: projects", ttype="section")
    ids["section_projects_soft"] = _add(e, uid, "Projects are not needed", key="section:projects",
                                        term="section: projects", ttype="section", strength=3)
    ids["section_achievements"] = _add(e, uid, "Lead with achievements", polarity="emphasize",
                                       key="section:achievements", term="section: achievements",
                                       ttype="section", strength=4)
    ids["family_soft_section"] = _add(e, uid, "Skip education for research roles", key="section:education",
                                      term="section: education", ttype="section", strength=3,
                                      scope="role_family", value="data_science")
    ids["kafka"] = _add(e, uid, "Never mention Kafka", term="Kafka")
    ids["torch"] = _add(e, uid, "Avoid torch", term="torch", strength=4)
    ids["snowflake"] = _add(e, uid, "Never mention Snowflake", term="Snowflake")
    ids["dangling_role"] = _add(e, uid, "Drop the research assistant role",
                                key="exp:research assistant|old lab", term="Research Assistant",
                                ttype="experience")
    ids["one_page"] = _add(e, uid, "Keep it to one page", polarity="emphasize", term="one page")
    ids["negated_page"] = _add(e, uid, "Avoid one-page resumes", term="one-page resumes", strength=3)
    return ids


# ── the outcomes ─────────────────────────────────────────────────────────────

def test_each_kind_of_row_gets_its_own_outcome(legacy, kg, isolated_engine):
    e, uid = isolated_engine, kg
    report = migrate_preferences(uid)

    # Pending terms that now name exactly one item are keyed copies (alias-aware: torch is PyTorch).
    relinked = {r["supersedes_id"]: r["key"] for r in report["relinked"]}
    assert relinked == {legacy["kafka"]: "skill:kafka", legacy["torch"]: "skill:pytorch"}
    (kafka,) = _active(e, uid, target_key="skill:kafka")
    assert kafka.text == "Never mention Kafka" and kafka.strength == 5 and kafka.polarity == "suppress"
    assert str(kafka.supersedes_id) == legacy["kafka"]
    assert kafka.provenance["migration"] == MIGRATION_ID and kafka.provenance["relinked_from"] == legacy["kafka"]
    assert _pref(e, legacy["kafka"]).status == "superseded"

    # A key whose item is gone is pending again, a role as "title @ company".
    gone = next(u for u in report["unlinked"] if u["preference_id"] == legacy["dangling_role"])
    assert gone["key"] == "exp:research assistant|old lab" and gone["term"] == "Research Assistant @ old lab"
    (pending,) = [p for p in _active(e, uid) if p.target_term == "Research Assistant @ old lab"]
    assert pending.target_key is None and pending.provenance["unlinked_from"] == gone["key"]
    # ... and so is the conftest set's `proj:coursework-db`, which no project carries.
    assert any(u["key"] == "proj:coursework-db" and u["term"] == "coursework-db" for u in report["unlinked"])

    # section rows: the suppress becomes a hard limit and keeps its scope; the soft duplicate reuses it.
    by_pref = {c["preference_id"]: c for c in report["converted_to_rules"]}
    hard = by_pref[legacy["section_projects"]]
    assert hard["params"] == {"projects": 0} and not hard["reused"]
    assert by_pref[legacy["section_projects_soft"]]["rule_id"] == hard["rule_id"]
    assert by_pref[legacy["section_projects_soft"]]["reused"]
    rules = {r["rule_id"]: r for r in services.load_resume_rules(uid)}
    assert rules[hard["rule_id"]]["strength"] == 5 and rules[hard["rule_id"]]["scope_type"] == "global"
    family = rules[by_pref[legacy["family_soft_section"]]["rule_id"]]
    assert family["params"] == {"education": 0} and family["strength"] == 3
    assert (family["scope_type"], family["scope_value"]) == ("role_family", "data_science")
    page = rules[by_pref[legacy["one_page"]]["rule_id"]]
    assert page["params"] == {"pages": 1} and page["strength"] == 5

    for key in ("section_projects", "section_projects_soft", "family_soft_section", "one_page"):
        old = _pref(e, legacy[key])
        assert old.status == "superseded"
        assert old.provenance["migrated_to"] == by_pref[legacy[key]]["rule_id"]
        assert old.provenance["migration"] == MIGRATION_ID
    assert rules[hard["rule_id"]]["provenance"]["migrated_from"] == legacy["section_projects"]

    # Everything else is left exactly as it was, with a reason.
    codes = _codes(report)
    assert codes[legacy["section_achievements"]] == "section_not_clear"
    assert codes[legacy["snowflake"]] == "pending"
    assert codes[legacy["negated_page"]] == "negated_limit"
    for key in ("section_achievements", "snowflake", "negated_page"):
        assert _pref(e, legacy[key]).status == "active"
        assert _pref(e, legacy[key]).provenance in (None, {})
    assert report["unchanged"] == 0
    c = report["counts"]
    assert (c["relinked"], c["unlinked"], c["converted_to_rules"]) == (2, 2, 4)


def test_nothing_is_deleted(legacy, kg, isolated_engine):
    e, uid = isolated_engine, kg
    with Session(e) as s:
        before = {str(p.preference_id): p.text for p in
                  s.exec(select(UserPreference).where(UserPreference.user_id == uid))}
    migrate_preferences(uid)
    with Session(e) as s:
        after = {str(p.preference_id): p.text for p in
                 s.exec(select(UserPreference).where(UserPreference.user_id == uid))}
    assert set(before) <= set(after) and len(after) > len(before)
    assert all(after[i] == before[i] for i in before)          # no text changed in place


def test_a_second_run_changes_nothing(legacy, kg, isolated_engine):
    e, uid = isolated_engine, kg
    first = migrate_preferences(uid)
    assert first["already_done"] == 0
    snap = _snapshot(e, uid)
    second = migrate_preferences(uid)
    assert _snapshot(e, uid) == snap
    for field in ("relinked", "unlinked", "converted_to_rules"):
        assert second[field] == [], field
    assert second["already_done"] > 0
    assert {r["preference_id"] for r in second["left_alone"]} >= {legacy["snowflake"]}
    assert migrate_preferences(uid)["already_done"] == second["already_done"]


def test_dry_run_reports_and_writes_nothing(legacy, kg, isolated_engine):
    e, uid = isolated_engine, kg
    snap = _snapshot(e, uid)
    dry = migrate_preferences(uid, dry_run=True)
    assert _snapshot(e, uid) == snap and dry["dry_run"] is True
    assert all(c["rule_id"] is None or c["reused"] for c in dry["converted_to_rules"])
    real = migrate_preferences(uid)
    assert dry["counts"] == real["counts"]
    assert {c["preference_id"] for c in dry["converted_to_rules"]} == \
           {c["preference_id"] for c in real["converted_to_rules"]}
    assert {r["key"] for r in dry["relinked"]} == {r["key"] for r in real["relinked"]}


def test_an_empty_store_is_a_no_op(isolated_engine):
    from conftest import _seed_user_and_skill
    uid = _seed_user_and_skill(isolated_engine).user_id
    report = migrate_preferences(uid)
    assert report["counts"]["active_rows"] == 0 and report["left_alone"] == []


# ── what is never converted ──────────────────────────────────────────────────

def test_a_page_limit_cannot_loosen_a_hard_rule(kg, isolated_engine):
    e, uid = isolated_engine, kg
    assert tools.record_rule(uid, "One page", "limit", {"pages": 1}, 5)["status"] == "stored"
    wide = _add(e, uid, "Two pages is fine for research roles", polarity="emphasize",
                term="two pages", scope="role_family", value="data_science", strength=3)
    report = migrate_preferences(uid)
    assert _codes(report)[wide] == "scope_conflict"
    assert _pref(e, wide).status == "active"
    assert [r["params"] for r in services.load_resume_rules(uid)] == [{"pages": 1}]


def test_a_rule_recorded_since_outranks_an_older_row(kg, isolated_engine):
    e, uid = isolated_engine, kg
    assert tools.record_rule(uid, "Two pages", "limit", {"pages": 2}, 3)["status"] == "stored"
    old = _add(e, uid, "Keep it to one page", polarity="emphasize", term="one page", strength=4)
    report = migrate_preferences(uid)
    assert _codes(report)[old] == "rule_conflict" and _pref(e, old).status == "active"
    assert [r["params"] for r in services.load_resume_rules(uid)] == [{"pages": 2}]


def test_a_rule_the_user_already_recorded_is_reused_not_duplicated(kg, isolated_engine):
    e, uid = isolated_engine, kg
    existing = tools.record_rule(uid, "No projects section", "limit", {"projects": 0}, 5)["rule"]
    old = _add(e, uid, "Never show projects", key="section:projects", term="section: projects",
               ttype="section")
    report = migrate_preferences(uid)
    (conv,) = report["converted_to_rules"]
    assert conv["rule_id"] == existing["rule_id"] and conv["reused"]
    assert len(services.load_resume_rules(uid)) == 1
    assert _pref(e, old).provenance["migrated_to"] == existing["rule_id"]


@pytest.mark.parametrize("text,polarity,key,term,code", [
    ("Don't drop the projects section", "suppress", "section:projects", "section: projects",
     "section_not_clear"),
    ("Lead with projects", "reframe", "section:projects", "section: projects", "section_not_clear"),
    ("Keep it to one page and no projects section", "suppress", None, "one page", "no_target"),
    ("Don't keep it to one page", "emphasize", None, "one page", "no_target"),
    ("Avoid one-page resumes", "suppress", None, "one page", "negated_limit"),
    ("Make it shorter", "emphasize", None, None, "no_target"),
])
def test_what_is_not_clear_cut_is_left_alone_and_never_given_a_number(
        text, polarity, key, term, code, kg, isolated_engine):
    e, uid = isolated_engine, kg
    pid = _add(e, uid, text, polarity=polarity, key=key, term=term,
               ttype="section" if key else "topic")
    report = migrate_preferences(uid)
    assert _codes(report)[pid] == code, report["left_alone"]
    assert _pref(e, pid).status == "active"
    assert services.load_resume_rules(uid) == []


def test_never_exceed_is_a_plain_maximum(kg, isolated_engine):
    e, uid = isolated_engine, kg
    pid = _add(e, uid, "Never exceed two pages", term="two pages")
    report = migrate_preferences(uid)
    (conv,) = report["converted_to_rules"]
    assert conv["preference_id"] == pid and conv["params"] == {"pages": 2}


def test_an_ambiguous_or_multiple_subject_waits_for_the_user(kg, isolated_engine):
    e, uid = isolated_engine, kg
    _skill(e, uid, "Spark")
    _skill(e, uid, "Kafka")
    both = _add(e, uid, "Never mention Kafka or Spark", term="Kafka or Spark")
    with Session(e) as s:                                 # a second "Python"-named project
        from database.models import Project
        s.add(Project(user_id=uid, name="Python", description="A second thing called Python"))
        s.commit()
    amb = _add(e, uid, "Never mention Python", term="Python")
    codes = _codes(migrate_preferences(uid))
    assert codes[both] == "multiple" and codes[amb] == "ambiguous"


def test_a_keyed_row_whose_item_exists_is_untouched(kg, isolated_engine):
    e, uid = isolated_engine, kg
    pid = _add(e, uid, "Never mention Python", key="skill:python", term="Python", ttype="skill")
    snap = _pref(e, pid).provenance
    report = migrate_preferences(uid)
    assert report["unchanged"] >= 1
    assert pid not in {u["preference_id"] for u in report["unlinked"]}
    assert _pref(e, pid).status == "active" and _pref(e, pid).provenance == snap


def test_an_unrecognized_key_is_left_alone(kg, isolated_engine):
    e, uid = isolated_engine, kg
    pid = _add(e, uid, "No gaps", key="topic:gaps")
    assert _codes(migrate_preferences(uid))[pid] == "unrecognized_key"


def test_no_items_means_no_key_is_judged_dangling(isolated_engine):
    """A store with preferences and no graph (an import in progress) cannot say a key is gone."""
    from conftest import _seed_user_and_skill
    uid = _seed_user_and_skill(isolated_engine).user_id
    with Session(isolated_engine) as s:
        for row in s.exec(select(UserSkill).where(UserSkill.user_id == uid)).all():
            s.delete(row)
        s.commit()
    pid = _add(isolated_engine, uid, "Never mention Kafka", key="skill:kafka", term="Kafka",
               ttype="skill")
    report = migrate_preferences(uid)
    assert _codes(report)[pid] == "no_items" and report["unlinked"] == []


def test_a_deleted_roles_ban_rebinds_only_to_its_own_key(kg, isolated_engine):
    """The #297 rule survives the migration: the pending copy carries `unlinked_from`, so a second
    role with the same title never inherits it."""
    e, uid = isolated_engine, kg
    from database.models import Experience
    with Session(e) as s:
        s.add(Experience(user_id=uid, title="Research Assistant", company="Other Lab", bullets=["x"]))
        s.commit()
    pid = _add(e, uid, "Drop the research assistant role", key="exp:research assistant|old lab",
               term="Research Assistant", ttype="experience")
    migrate_preferences(uid)
    again = migrate_preferences(uid)
    assert again["relinked"] == [] and again["unlinked"] == []
    assert _pref(e, pid).status == "superseded"
    assert [p.target_key for p in _active(e, uid, target_term="Research Assistant @ old lab")] == [None]


# ── enforcement is equal or better ───────────────────────────────────────────

def test_the_section_exclusion_now_removes_the_section(env, isolated_engine):
    """Before: the `section:` row is enforced as the term "section: projects", which no bullet holds,
    so a plan keeps the project. After: a hard `limit` rule, and the section is gone."""
    from harness import executor, tree
    uid, job_id, _ = env
    _add(isolated_engine, uid, "Never show the projects section", key="section:projects",
         term="section: projects", ttype="section")
    plan = {"job_id": job_id, "nodes": [{"id": "p", "op": "keep", "item_key": PROJ}]}
    before = _run(uid, plan, dry_run=True)
    assert {n["id"]: n["status"] for n in before["nodes"]}["p"] == "kept"

    report = migrate_preferences(uid)
    assert [c["params"] for c in report["converted_to_rules"]] == [{"projects": 0}]
    after = _run(uid, plan)
    status = {n["id"]: n for n in after["nodes"]}
    assert status["p"]["status"] == "refused" and status["p"]["reason"].startswith("page_rule:")
    content = tree.get_head(uid, job_id)["head"]["content"]
    assert content["projects"] == [] and content["_excluded_sections"] == ["projects"]
    # the old row no longer pins anything: it is superseded, the rule enforces it
    suppress, _, _ = executor._hard_preferences(uid, None)
    assert not [p for p in suppress.values() if "projects section" in p["text"]]


def test_a_left_alone_row_is_enforced_as_before(env, isolated_engine):
    """A pending ban stays a term pin; migrating must not weaken what was enforced."""
    from uuid import UUID

    from harness import executor
    uid, job_id, _ = env
    _add(isolated_engine, uid, "Never mention Snowflake", term="Snowflake")
    before = executor._hard_preferences(uid, UUID(job_id))
    assert set(before[2]) == {"snowflake"}
    migrate_preferences(uid)
    after = executor._hard_preferences(uid, UUID(job_id))
    assert [sorted(d) for d in after] == [sorted(d) for d in before]


def test_a_relinked_pin_is_still_a_pin_and_now_matches_aliases(env, isolated_engine):
    from uuid import UUID

    from harness import executor
    from harness.decisions.negative_pins import pin_target
    uid, job_id, _ = env
    _skill(isolated_engine, uid, "Kafka")
    _add(isolated_engine, uid, "Never mention Kafka", term="Kafka")
    before = executor._hard_preferences(uid, UUID(job_id))[2]
    assert set(before) == {"kafka"} and pin_target(before["kafka"])[0] == "term"
    migrate_preferences(uid)
    after = executor._hard_preferences(uid, UUID(job_id))[2]
    assert set(after) == {"kafka"} and pin_target(after["kafka"])[0] == "skill"
    assert after["kafka"]["text"] == "Never mention Kafka" and after["kafka"]["target_key"] == "skill:kafka"


# ── how it runs ──────────────────────────────────────────────────────────────

def test_the_cli_reports_a_dry_run_and_then_migrates(legacy, kg, isolated_engine, monkeypatch, capsys):
    e, uid = isolated_engine, kg
    monkeypatch.setattr("harness.runtime.bootstrap", lambda *a, **k: (uid, True))
    snap = _snapshot(e, uid)
    assert migrations.main(["preferences", "--dry-run"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] is True and dry["counts"]["converted_to_rules"] == 4
    assert _snapshot(e, uid) == snap

    from harness import entry
    assert entry.main(["migrate", "preferences"]) == 0
    done = json.loads(capsys.readouterr().out)
    assert done["dry_run"] is False and done["counts"]["converted_to_rules"] == 4
    assert len(services.load_resume_rules(uid)) == 3          # one rule is shared by two rows
    assert entry.main(["migrate", "preferences"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["converted_to_rules"] == 0


def test_the_cli_refuses_to_write_to_a_read_only_store(legacy, kg, isolated_engine, monkeypatch, capsys):
    e, uid = isolated_engine, kg
    monkeypatch.setattr("harness.runtime.bootstrap", lambda *a, **k: (uid, False))
    snap = _snapshot(e, uid)
    assert migrations.main(["preferences"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "read_only"
    assert migrations.main(["preferences", "--dry-run"]) == 0       # a read is always allowed
    assert _snapshot(e, uid) == snap


def test_the_automatic_run_is_once_per_user_on_a_local_store(legacy, kg, isolated_engine, tmp_path):
    uid = kg
    url = f"sqlite:///{tmp_path / 'art.db'}"
    first = migrations.run_once(uid, url)
    assert first is not None and first["counts"]["converted_to_rules"] == 4
    marker = json.loads((tmp_path / migrations.MARKER_FILE).read_text())
    assert str(uid) in marker[MIGRATION_ID]
    assert migrations.run_once(uid, url) is None                  # already done for this user


def test_the_automatic_run_never_touches_a_remote_store(legacy, kg, isolated_engine, tmp_path):
    e, uid = isolated_engine, kg
    snap = _snapshot(e, uid)
    assert migrations.run_once(uid, "postgresql://u:p@db.example.com:5432/art") is None
    assert _snapshot(e, uid) == snap


def test_a_failed_automatic_run_is_swallowed_and_not_marked_done(kg, isolated_engine, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("the store went away")
    monkeypatch.setattr(migrations, "migrate_preferences", boom)
    url = f"sqlite:///{tmp_path / 'art.db'}"
    assert migrations.run_once(kg, url) is None
    assert not (tmp_path / migrations.MARKER_FILE).exists()


def test_a_store_with_no_preferences_is_not_marked_done(isolated_engine, tmp_path):
    from conftest import _seed_user_and_skill
    uid = _seed_user_and_skill(isolated_engine).user_id
    url = f"sqlite:///{tmp_path / 'art.db'}"
    assert migrations.run_once(uid, url)["counts"]["active_rows"] == 0
    assert not (tmp_path / migrations.MARKER_FILE).exists()       # rows imported later still migrate


def test_bootstrap_runs_it_for_a_prepared_local_store_only(kg, tmp_path, monkeypatch):
    from harness import runtime
    calls = []
    monkeypatch.setattr(migrations, "run_once", lambda uid, url: calls.append((uid, url)))
    monkeypatch.setattr(runtime, "prepare_local_store", lambda url: url.startswith("sqlite"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///restored.db")
    url = f"sqlite:///{tmp_path / 'art.db'}"

    runtime.bootstrap(url, str(kg), prepare=True)
    assert calls == [(kg, url)]
    runtime.bootstrap(url, str(kg), prepare=True, auto_migrate=False)
    runtime.bootstrap(url, str(kg), prepare=False)               # hooks: nothing writes
    runtime.bootstrap("postgresql://u:p@db.example.com/art", str(kg), allow_writes=True, prepare=True)
    assert calls == [(kg, url)]


def test_bootstrap_survives_a_migration_that_fails(kg, tmp_path, monkeypatch):
    from harness import runtime
    monkeypatch.setattr(migrations, "migrate_preferences",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(runtime, "prepare_local_store", lambda url: True)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///restored.db")
    uid, writes = runtime.bootstrap(f"sqlite:///{tmp_path / 'art.db'}", str(kg), prepare=True)
    assert uid == kg and writes is True


def test_importing_the_module_does_not_build_an_engine():
    """`art migrate` reaches this module before `bootstrap` pins DATABASE_URL: a module-level import
    of `services` would connect to whatever `.env` holds (found by running it against a store copy)."""
    import subprocess
    import sys
    code = ("import sys, harness.migrations, harness.entry\n"
            "bad = [m for m in ('services', 'database.db', 'config') if m in sys.modules]\n"
            "sys.exit(1 if bad else 0)")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
