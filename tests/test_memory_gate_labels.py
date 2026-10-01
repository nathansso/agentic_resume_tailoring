"""The labelled set for fitting the memory gate, and its recordings (issue #202).

`eval/memory_gate_labels/pairs.json` holds synthetic user messages labelled by hand over one synthetic
benchmark profile's catalog; `recordings.json` holds the real Jev answers to `memory_gate@v1` for every
message, recorded once. Nothing here calls the API: the key is removed from every test's environment, and
the replay tests fail on any miss instead of falling back.
"""

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import Session

from eval import fit_memory_gate_threshold as fit
from harness import memory
from harness.decisions import engine, memory_gate as mg, recordings
from harness.decisions.client import JevReplayMiss
from harness.decisions.questions import Answer

ROOT = Path(__file__).resolve().parent.parent
DOC = fit.load_doc()
PAIRS = DOC["pairs"]
CONTEXT = DOC["context_pairs"]
RECORDED = json.loads(fit.RECORDINGS_PATH.read_text(encoding="utf-8"))
NOT_PREFERENCES = ("one_off_edit", "question", "experience_fact", "chit_chat")
ABSOLUTE = re.compile(r"\bnever\b|\bever\b|under no circumstances", re.IGNORECASE)


@pytest.fixture()
def replayed(isolated_engine, monkeypatch):
    """Every message replayed once, offline, from the recordings: `(rows, context_rows, catalog, stats)`."""
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    monkeypatch.setattr(engine, "get_client", lambda: pytest.fail("replay reached for a client"))
    recordings.import_recordings(RECORDED)
    return fit.replay_rows(DOC)


# ── the set ──────────────────────────────────────────────────────────────────

def test_the_set_is_versioned_commented_and_large_enough():
    assert DOC["_comment"] and DOC["version"] == 1
    assert len(PAIRS) >= 90 and len({p["id"] for p in PAIRS + CONTEXT}) == len(PAIRS) + len(CONTEXT)
    for p in PAIRS + CONTEXT:
        assert {"id", "category", "message", "is_preference", "direction", "strength", "target", "job_scoped",
                "rationale"} <= set(p), p["id"]
        assert p["message"].strip() and p["rationale"].strip()
        assert p["direction"] in DOC["directions"]


def test_every_category_has_at_least_eight_messages():
    by_cat = Counter(p["category"] for p in PAIRS)
    assert set(by_cat) == set(DOC["categories"]) == set(fit.CATEGORY_ORDER)
    assert all(n >= 8 for n in by_cat.values()), by_cat


def test_the_labels_mean_what_the_categories_say():
    for p in PAIRS:
        if p["category"] in NOT_PREFERENCES:
            assert (p["is_preference"], p["direction"], p["strength"], p["target"], p["job_scoped"]) == (
                False, "none", None, None, False), p["id"]
            continue
        assert p["is_preference"] and p["direction"] in ("emphasize", "suppress", "format_rule"), p["id"]
        assert p["strength"] in (1, 2, 3, 4, 5), p["id"]
        assert p["job_scoped"] == (p["category"] == "job_scoped"), p["id"]
    cats = {c: {p["direction"] for p in PAIRS if p["category"] == c} for c in DOC["categories"]}
    assert cats["explicit_positive"] == {"emphasize"} and cats["explicit_negative"] == {"suppress"}
    assert cats["format_rule"] == {"format_rule"} and {"emphasize", "suppress"} <= cats["double_negation"]


def test_strength_five_is_only_for_absolute_wording_and_all_absolute_wording_is_five():
    for p in PAIRS:
        if p["is_preference"]:
            assert (p["strength"] == 5) == bool(ABSOLUTE.search(p["message"])), (p["id"], p["strength"])


def test_the_negation_categories_hold_double_and_embedded_negations_of_both_directions():
    double = [p for p in PAIRS if p["category"] == "double_negation"]
    assert len(double) >= 8
    assert all(mg.prefilter(p["message"]).negated for p in double)
    assert sum(p["direction"] == "emphasize" for p in double) >= 6       # "don't stop", "don't leave out": a negation that means keep
    assert sum(p["direction"] == "suppress" for p in double) >= 1        # "don't keep": a negation that means leave out
    assert sum(p["direction"] == "suppress" for p in PAIRS if p["category"] == "explicit_negative") >= 8


def test_the_set_includes_the_hard_negatives_a_cue_word_would_get_wrong():
    cue_non_prefs = [p for p in PAIRS if p["category"] in NOT_PREFERENCES and mg.prefilter(p["message"]).candidate]
    assert len(cue_non_prefs) >= 10                  # "never shipped", "don't have", "should I keep", "stop, that's too long"
    assert {p["category"] for p in cue_non_prefs} == set(NOT_PREFERENCES)
    implicit = [p for p in PAIRS if p["category"] == "implicit"]
    assert sum(not mg.prefilter(p["message"]).candidate for p in implicit) >= 5   # the prefilter misses these by design


def test_targets_are_catalog_keys_and_the_catalog_is_one_synthetic_profile():
    catalog = fit.profile_catalog()
    keys = {c["key"] for c in catalog}
    assert (ROOT / "eval" / "profiles" / f"{DOC['profile']}.md").is_file()        # never personal/
    assert "personal" not in json.dumps([(p["message"], p["rationale"]) for p in PAIRS + CONTEXT]).lower()
    for p in PAIRS + CONTEXT:
        assert p["target"] is None or p["target"] in keys, (p["id"], p["target"])
    assert sum(p["target"] is None and p["is_preference"] for p in PAIRS) >= 5      # topics and general rules
    assert 20 <= len(catalog) <= mg.MAX_CATALOG and all(c["key"].split(":")[0] in ("exp", "proj", "skill", "section")
                                                       for c in catalog)


def test_the_context_pairs_lean_on_the_previous_turn():
    assert len(CONTEXT) >= 10 and all(c["previous"].strip() and c["category"] == "context" for c in CONTEXT)
    assert sum(c["is_preference"] for c in CONTEXT) >= 6 and sum(not c["is_preference"] for c in CONTEXT) >= 3


# ── the recordings ───────────────────────────────────────────────────────────

def test_every_message_replays_from_the_recordings_with_no_live_call(replayed):
    rows, ctx, _, stats = replayed
    assert len(rows) == len(PAIRS) and len(ctx) == len(CONTEXT)
    assert stats["hit_rate"] == 1.0 and stats["jev"] == 0 and stats["fallback"] == 0 and stats["requests"] == 0
    # 4 questions per message; a context message is asked twice (alone, and with the previous turn)
    assert stats["cache"] == 4 * (len(PAIRS) + 2 * len(CONTEXT))
    assert all(r["guess"] is not None and r["guess"].source == "cache" for r in rows)
    assert all(0.0 <= r["guess"].p <= 1.0 and 1 <= r["guess"].strength <= 5 for r in rows)


def test_a_changed_message_fails_loudly_instead_of_falling_back(isolated_engine, monkeypatch):
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    monkeypatch.setattr(engine, "get_client", lambda: pytest.fail("replay reached for a client"))
    recordings.import_recordings(RECORDED)
    catalog = fit.profile_catalog()
    ok = mg.ask(PAIRS[0]["message"], catalog)
    assert all(a.source == "cache" for a in ok)
    with pytest.raises(JevReplayMiss):
        mg.ask(PAIRS[0]["message"] + " Really.", catalog)


def test_the_recordings_carry_no_message_text():
    blob = json.dumps(RECORDED["decisions"])
    assert not any(p["message"] in blob for p in PAIRS + CONTEXT if len(p["message"]) > 20)   # "ok" is in everything
    assert {d["point"] for d in RECORDED["decisions"]} == {mg.POINT}
    assert {d["question_version"] for d in RECORDED["decisions"]} == {mg.VERSION}
    assert {d["resolved_model"] for d in RECORDED["decisions"]} == {"jev-1.13.0"}


def test_the_recordings_were_made_with_the_questions_the_gate_asks_today(isolated_engine, monkeypatch):
    """A reworded question changes the cache key: this is the test that says to re-record."""
    from harness.decisions import cache

    catalog = fit.profile_catalog()
    keys = {d["cache_key"] for d in RECORDED["decisions"]}
    for q in mg.questions_for(catalog):
        assert cache.cache_key(mg.build_state(PAIRS[0]["message"]), q, "jev-1.13.0") in keys


# ── what the gate does with real answers ─────────────────────────────────────

def _seed_profile(isolated_engine):
    """A user holding the benchmark profile the recordings were made against."""
    from database.models import Experience, Project, Skill, User, UserSkill
    from eval.profile_fixture import load_profile

    prof = load_profile(ROOT / "eval" / "profiles" / f"{DOC['profile']}.md")
    with Session(isolated_engine) as s:
        user = User(name="Gate Test", email="gate@example.com")
        s.add(user)
        s.commit()
        s.refresh(user)
        for sk in prof.skills:
            skill = Skill(name=sk["name"], category=sk["category"])
            s.add(skill)
            s.commit()
            s.refresh(skill)
            s.add(UserSkill(user_id=user.user_id, skill_id=skill.skill_id, proficiency=sk["proficiency"]))
        for e in prof.experiences:
            s.add(Experience(user_id=user.user_id, title=e["title"], company=e["company"], bullets=e["bullets"]))
        for p in prof.projects:
            s.add(Project(user_id=user.user_id, name=p["name"], description=p.get("description") or ""))
        s.commit()
        return user.user_id


@pytest.fixture()
def recorded_gate(isolated_engine, monkeypatch):
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    monkeypatch.setattr(engine, "get_client", lambda: None)
    recordings.import_recordings(RECORDED)
    return _seed_profile(isolated_engine)


def _msg(id_):
    return next(p["message"] for p in PAIRS if p["id"] == id_)


def test_the_gates_catalog_for_a_stored_profile_is_the_one_the_recordings_were_made_with(recorded_gate):
    assert memory.memory_catalog(recorded_gate) == fit.profile_catalog()


def test_real_answers_write_a_clear_preference_and_hand_the_hard_ones_to_the_host(recorded_gate, isolated_engine):
    from services import load_preferences

    wrote = memory.observe(recorded_gate, _msg("ep_python_first"), "s")
    assert (wrote["action"], wrote["source"]) == ("write", "cache")
    [pref] = [p for p in load_preferences(recorded_gate) if p["target_key"] == "skill:python"]
    assert (pref["polarity"], pref["strength"], pref["scope_type"]) == ("emphasize", 4, "global")
    assert pref["provenance"]["source"] == "memory_gate"

    pin = memory.observe(recorded_gate, _msg("en_excel"), "s")                       # "Never mention Excel"
    assert (pin["action"], pin["reason"]) == ("host", "hard_preference") and pin["guess"]["strength"] == 5
    assert not [p for p in load_preferences(recorded_gate) if p["target_key"] == "skill:excel"]

    dbl = memory.observe(recorded_gate, _msg("dn_docker_stop"), "s")                 # "Don't stop mentioning Docker"
    assert (dbl["action"], dbl["reason"]) == ("host", "negation_disagrees")

    gpa = memory.observe(recorded_gate, _msg("en_gpa"), "s")                         # bound to the education section
    assert gpa["action"] == "host" and gpa["guess"]["target"] == "section:education" and not gpa["guess"]["target_named"]

    assert memory.observe(recorded_gate, _msg("cc_thanks"), "s")["action"] == "drop"
    assert memory.observe(recorded_gate, _msg("xf_never_shipped"), "s")["action"] == "drop"   # a cue, but a fact
    ask = memory.observe(recorded_gate, _msg("q_never_excel"), "s")                           # a cue, but a question
    assert ask["action"] != "write" and ask["p"] < 0.2


def test_a_job_scoped_message_from_the_recordings_is_written_scoped_only_with_a_known_job(recorded_gate):
    from harness import hooks
    from services import load_preferences

    msg = _msg("js_dbt")
    assert memory.observe(recorded_gate, msg, "no-job")["reason"] == "job_unknown"
    job = str(uuid4())
    hooks.save_state("with-job", {"cursor": 0, "job_id": job})
    d = memory.observe(recorded_gate, msg, "with-job")
    assert (d["action"], d["scope"]) == ("write", "job")
    [pref] = [p for p in load_preferences(recorded_gate) if p["target_key"] == "skill:dbt"]
    assert (pref["scope_type"], pref["scope_value"]) == ("job", job)


# ── the fit ──────────────────────────────────────────────────────────────────

def _row(id_, message, *, pref, p, direction="emphasize", strength=3, target="skill:python", target_p=0.99,
         named=True, label_direction=None, label_target="skill:python", label_strength=3):
    guess = mg.Guess(p=p, direction=direction, direction_p=0.9, strength=strength, strength_confidence=0.9, hard_p=0.0,
                     target_key=target, target_label="x", target_p=target_p, target_named=named, source="jev")
    return {"id": id_, "category": "explicit_positive", "message": message, "pre": mg.prefilter(message),
            "guess": guess, "is_preference": pref, "direction": label_direction or direction,
            "strength": label_strength if pref else None, "target": label_target if pref else None,
            "job_scoped": False, "jev_pref": p >= 0.5}


def test_tau_hi_is_zero_wrong_writes_first_then_the_most_writes_then_the_middle_of_the_gap():
    msg = "Always lead with Python."
    rows = [_row("a", msg, pref=True, p=0.95), _row("b", msg, pref=True, p=0.80), _row("c", msg, pref=True, p=0.72),
            _row("w", msg, pref=True, p=0.60, label_direction="suppress"),          # a wrong-way write at 0.60
            _row("n", msg, pref=False, p=0.30)]                                      # a non-preference
    hi = fit.fit_hi(rows, 0.0, 0.5)
    assert hi["wrong"] == 0 and hi["autos"] == 3
    # the wrong write scores 0.60 and the lowest right one 0.72: the middle is 0.66, so 0.65
    assert hi["tau_hi"] == 0.65 and hi["danger_max"] == 0.60 and hi["kept_min"] == 0.72


def test_tau_lo_drops_no_true_preference_then_hands_the_fewest_non_preferences_to_the_host():
    msg = "Always lead with Python."
    rows = [_row("p1", msg, pref=True, p=0.90), _row("p2", msg, pref=True, p=0.40),
            _row("n1", msg, pref=False, p=0.30), _row("n2", msg, pref=False, p=0.10)]
    lo = fit.fit_lo(rows, 0.65)
    assert lo["met"] and lo["dropped_prefs"] == 0 and lo["handoffs"] == 0
    assert lo["tau_lo"] == 0.35                                    # between 0.30 (a non-preference) and 0.40 (a preference)
    rows.append(_row("p3", msg, pref=True, p=0.05))                # a preference no grid value below it can keep
    lo = fit.fit_lo(rows, 0.65)
    assert lo["tau_lo"] == 0.05 and lo["dropped_prefs"] == 0


def test_tau_target_takes_no_wrong_binding_and_ignores_one_the_message_does_not_name():
    msg = "Leave my GPA off every resume."
    rows = [_row("r", msg, pref=True, p=0.9, target_p=0.99), _row("r2", msg, pref=True, p=0.9, target_p=0.85),
            _row("w", msg, pref=True, p=0.9, target="section:education", target_p=0.70, label_target="skill:python"),
            _row("u", msg, pref=True, p=0.9, target="section:education", target_p=0.92, named=False,
                 label_target="skill:python")]                                      # not named: the name check owns it
    tg = fit.fit_target(rows)
    assert tg["wrong"] == 1 and tg["unnamed_wrong_ids"] == ["u"] and tg["kept_right"] == 2
    assert tg["tau_target"] == 0.8             # 0.75, 0.80 and 0.85 all keep both right ones; 0.775 is the middle, ties go up


def test_a_wrong_write_is_a_non_preference_or_a_wrong_direction_target_scope_or_strength():
    msg = "Always lead with Python."
    d = lambda r: fit.route_row(r, 0.25, 0.65, 0.75)  # noqa: E731
    ok = _row("ok", msg, pref=True, p=0.9)
    assert d(ok)["action"] == "write" and not fit.write_is_wrong(ok, d(ok))
    assert fit.write_is_wrong(_row("x", msg, pref=False, p=0.9), d(_row("x", msg, pref=False, p=0.9)))
    for kw in (dict(label_direction="suppress"), dict(label_target="skill:sql"), dict(label_strength=1)):
        r = _row("y", msg, pref=True, p=0.9, **kw)
        assert fit.write_is_wrong(r, d(r)), kw
    assert not fit.write_is_wrong(_row("z", msg, pref=True, p=0.9, label_strength=2), d(ok))   # one level off is within tolerance


# ── the numbers the gate ships with ──────────────────────────────────────────

def test_the_constants_in_memory_gate_equal_the_fit(replayed):
    rows = replayed[0]
    rec = fit.recommend(rows)
    assert (mg.TAU_LO, mg.TAU_HI, mg.TAU_TARGET) == (rec["tau_lo"], rec["tau_hi"], rec["tau_target"])
    assert (mg.TAU_LO, mg.TAU_HI, mg.TAU_TARGET) == (0.25, 0.65, 0.75)
    assert mg.TAU_LO < mg.TAU_HI and 0 < mg.TAU_TARGET < 1


def test_tau_hi_has_a_hard_floor_a_write_needs_jev_to_say_more_likely_than_not(replayed):
    rows = replayed[0]
    assert mg.HI_FLOOR == 0.5 and mg.TAU_HI >= mg.HI_FLOOR
    hi = fit.fit_hi(rows, 0.0, mg.TAU_TARGET)
    assert min(hi["table"]) >= mg.HI_FLOOR and hi["tau_hi"] >= mg.HI_FLOOR
    # even a set with no wrong write anywhere cannot pull it under the floor
    safe = [r for r in rows if r["id"] not in hi["danger_ids"]]
    assert fit.fit_hi(safe, 0.0, mg.TAU_TARGET)["tau_hi"] >= mg.HI_FLOOR
    assert fit.recommend(safe)["tau_hi"] >= mg.HI_FLOOR


def test_tau_hi_is_fitted_as_if_the_code_rules_did_not_exist(replayed):
    """The section rule, the strength-5 rule and the negation backstop only remove writes, so they must
    never loosen a threshold: js_edu_first is a wrong write without them and still sets the boundary."""
    rows = replayed[0]
    assert set(fit.FIT_WITHOUT) == {"hard", "section", "backstop"} and set(fit.FIT_WITHOUT) <= set(mg.CODE_RULES)
    hi = fit.fit_hi(rows, 0.0, mg.TAU_TARGET)
    assert hi["without"] == list(fit.FIT_WITHOUT) and hi["tau_hi"] == mg.TAU_HI == 0.65
    assert "js_edu_first" in hi["danger_ids"] and hi["danger_id"] == "js_edu_first" and hi["danger_max"] == 0.63
    assert hi["wrong"] == 0 and hi["kept_min"] == 0.68 and hi["candidates"] == [0.65]
    # with the rules on (what the gate does) the same row is a host hand-off, yet the fit does not use that
    on = fit.fit_hi(rows, 0.0, mg.TAU_TARGET, without=())
    assert on["tau_hi"] == mg.HI_FLOOR and on["danger_ids"] == []
    assert on["tau_hi"] != hi["tau_hi"]                            # fitted that way the threshold would loosen
    # dropping the name check as well does not move it
    assert fit.fit_hi(rows, 0.0, mg.TAU_TARGET, fit.FIT_WITHOUT + ("named",))["tau_hi"] == 0.65


def test_the_gate_writes_no_wrong_preference_on_the_set_and_never_a_hard_one(replayed):
    rows = replayed[0]
    decisions = {r["id"]: fit.route_row(r) for r in rows}
    writes = [r for r in rows if decisions[r["id"]]["action"] == "write"]
    assert len(writes) >= 10                                           # the gate is useful, not just safe
    assert not [r["id"] for r in writes if fit.write_is_wrong(r, decisions[r["id"]])]
    assert all(decisions[r["id"]]["guess"]["strength"] <= mg.AUTO_MAX_STRENGTH for r in writes)
    assert all(decisions[r["id"]]["guess"]["hard_p"] < mg.HARD_MASS for r in writes)
    # even with every threshold open, a strength-5 guess is never written
    for r in rows:
        d = fit.route_row(r, 0.0, 0.0, 0.0)
        if d["action"] == "write":
            assert d["guess"]["strength"] < 5 and d["guess"]["hard_p"] < mg.HARD_MASS, r["id"]


def test_every_true_preference_the_gate_is_asked_about_is_kept_and_no_non_preference_is_written(replayed):
    rows = replayed[0]
    reach = [r for r in rows if fit.reachable(r)]
    assert all(fit.route_row(r)["action"] != "drop" for r in reach if r["is_preference"])
    assert not [r["id"] for r in rows if not r["is_preference"] and fit.route_row(r)["action"] == "write"]
    handed = [r for r in reach if not r["is_preference"] and fit.route_row(r)["action"] == "host"]
    assert len(handed) <= 4                                    # the few non-preferences between TAU_LO and TAU_HI


def test_a_preference_about_a_whole_section_goes_to_the_host_at_every_threshold(replayed):
    rows = replayed[0]
    by_id = {r["id"]: r for r in rows}
    assert fit.route_row(by_id["js_edu_first"])["action"] == "host"                  # 0.63: under TAU_HI at the shipped values
    d = fit.route_row(by_id["js_edu_first"], 0.0, 0.0, 0.0)                         # and with every threshold open
    assert (d["action"], d["reason"]) == ("host", "section_target")
    for t in fit.GRID:
        for lo in (0.0, 0.05):
            for r in rows:
                d = fit.route_row(r, lo, t, 0.0)
                if ((d["guess"] or {}).get("target") or "").startswith("section:"):
                    assert d["action"] != "write", (r["id"], t)
    sections = [r for r in rows if r["is_preference"] and (r["target"] or "").startswith("section:")]
    assert len(sections) >= 8 and all(fit.route_row(r)["action"] != "write" for r in sections)


def test_the_thresholds_sit_inside_their_gaps(replayed):
    rows = replayed[0]
    writes = [r for r in rows if fit.route_row(r)["action"] == "write"]
    assert len(writes) == 15 and min(r["guess"].p for r in writes) >= mg.TAU_HI
    # without the code rules the lowest right write is 0.03 above TAU_HI and the highest wrong one 0.02 below it
    bare = [r for r in rows if fit.route_row(r, mg.TAU_LO, mg.TAU_HI, mg.TAU_TARGET, without=fit.FIT_WITHOUT)["action"] == "write"]
    assert len(bare) == 29 and min(r["guess"].p for r in bare) - mg.TAU_HI <= 0.05
    prefs = [r for r in rows if fit.reachable(r) and r["is_preference"]]
    assert min(r["guess"].p for r in prefs) >= mg.TAU_LO + 0.05


def test_jev_beats_the_heuristics_alone_on_precision_without_losing_recall(replayed):
    rows = replayed[0]
    truth = lambda r: r["is_preference"]  # noqa: E731
    heur = fit.prf(rows, truth, lambda r: r["pre"].candidate)
    kept = fit.prf(rows, truth, lambda r: fit.route_row(r)["action"] != "drop")
    jev = fit.prf(rows, truth, lambda r: r["jev_pref"])
    assert kept["precision"] >= 0.9 and heur["precision"] < 0.85
    wrote = fit.prf(rows, truth, lambda r: fit.route_row(r)["action"] == "write")
    assert wrote["precision"] == 1.0 and wrote["tp"] == 15
    assert kept["recall"] >= heur["recall"]                          # the prefilter bounds recall; Jev adds none and removes none
    assert jev["precision"] == 1.0 and jev["recall"] > 0.7


def test_the_negation_categories_are_measured_on_their_own_and_jev_reads_them(replayed):
    rows = replayed[0]
    neg = [r for r in rows if r["category"] in fit.NEGATION_CATEGORIES]
    stats = fit.direction_stats(neg)
    assert stats["n"] >= 20 and stats["jev"] / stats["n"] >= 0.9
    assert stats["heuristic"] / stats["n"] < 0.7                       # a negation cue alone cannot read a double negation
    double = [r for r in rows if r["category"] == "double_negation"]
    assert sum(r["guess"].direction == r["direction"] for r in double) >= len(double) - 1


def test_strength_agrees_within_one_level_and_a_pin_is_called_a_pin(replayed):
    rows = replayed[0]
    s = fit.strength_stats(rows)
    assert s["within1"] / s["n"] >= 0.9
    labelled_five = [r for r in rows if r["strength"] == 5]
    assert sum(r["guess"].strength == 5 or r["guess"].hard_p >= mg.HARD_MASS for r in labelled_five) == len(labelled_five)


# ── the reports ──────────────────────────────────────────────────────────────

def test_the_committed_report_and_review_are_current(replayed):
    rows, ctx, catalog, stats = replayed
    meta = fit._meta(RECORDED, stats["hit_rate"], catalog)
    assert fit.REPORT_PATH.read_text(encoding="utf-8") == fit.render_report(rows, ctx, meta) + "\n"
    assert fit.REVIEW_PATH.read_text(encoding="utf-8") == fit.render_review(rows, ctx) + "\n"


def test_the_review_lists_disagreements_first_and_every_message(replayed):
    text = fit.REVIEW_PATH.read_text(encoding="utf-8")
    assert text.index("## Disagreements with Jev") < text.index("## Agreements") < text.index("## Context messages")
    for p in PAIRS + CONTEXT:
        assert f"`{p['id']}`" in text
    assert "user confirmed" in text


def test_analyze_runs_offline_from_the_command_line(tmp_path):
    env = {k: v for k, v in __import__("os").environ.items() if k not in ("TYPESAFE_API_KEY", "DATABASE_URL")}
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.run([sys.executable, str(ROOT / "eval" / "fit_memory_gate_threshold.py"), "analyze",
                           "--report", str(tmp_path / "r.md"), "--review", str(tmp_path / "v.md")],
                          capture_output=True, text=True, cwd=ROOT, env=env, timeout=240)
    assert proc.returncode == 0, proc.stderr
    assert "hit rate 100%" in proc.stdout
    assert (tmp_path / "r.md").read_text(encoding="utf-8") == fit.REPORT_PATH.read_text(encoding="utf-8")
