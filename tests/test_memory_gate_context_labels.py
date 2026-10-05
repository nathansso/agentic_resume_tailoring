"""The context set for `memory_gate@v2`, its recordings and the thresholds fitted on them (issue #244).

`eval/memory_gate_labels/context/pairs.json` holds 40 synthetic messages that lean on the assistant turn before
them, each with that turn; `context/recordings.json` holds the real Jev answers to `memory_gate@v1` (the message
alone) and `memory_gate@v2` (with the turn) that #202's recordings did not already hold. Nothing here calls the
API: the key is removed from every test's environment and a replay miss raises.
"""

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from eval import fit_memory_gate_context as ctx
from eval import fit_memory_gate_threshold as base
from harness.decisions import cache, engine, memory_gate as mg, recordings
from harness.decisions.client import JevReplayMiss

ROOT = Path(__file__).resolve().parent.parent
DOC = ctx.load_doc()
PAIRS = DOC["pairs"]
MAIN = base.load_doc()
RECORDED = json.loads(ctx.RECORDINGS_PATH.read_text(encoding="utf-8"))
PARENT = json.loads(base.RECORDINGS_PATH.read_text(encoding="utf-8"))
MODEL = "jev-1.13.0"
ABSOLUTE = re.compile(r"(?<![a-z])(?:never|ever|under no circumstances)(?![a-z])", re.IGNORECASE)


@pytest.fixture()
def replayed(isolated_engine, monkeypatch):
    """Every pair replayed once, offline, alone and with its turn: `(rows, catalog, stats)`."""
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    monkeypatch.setattr(engine, "get_client", lambda: pytest.fail("replay reached for a client"))
    recordings.import_recordings(PARENT)
    recordings.import_recordings(RECORDED)
    return ctx.replay_rows(DOC)


# ── the set ──────────────────────────────────────────────────────────────────

def test_the_set_has_forty_context_messages_the_twelve_from_202_and_the_new_ones():
    assert DOC["version"] == 1 and DOC["_comment"] and len(PAIRS) >= 40
    assert len({p["id"] for p in PAIRS}) == len(PAIRS)
    assert Counter(p["origin"] for p in PAIRS) == {"202": 12, "244": len(PAIRS) - 12}
    for p in PAIRS:
        assert {"id", "category", "previous", "message", "is_preference", "direction", "strength", "target",
                "job_scoped", "rationale", "origin"} <= set(p), p["id"]
        assert p["previous"].strip() and p["message"].strip() and p["rationale"].strip()
        assert p["direction"] in DOC["directions"] and p["category"] in DOC["categories"]


def test_the_twelve_from_202_keep_their_messages_turns_and_confirmed_labels():
    old = {p["id"]: p for p in MAIN["context_pairs"]}
    mine = {p["id"]: p for p in PAIRS if p["origin"] == "202"}
    assert set(old) == set(mine)
    for id_, p in old.items():
        for key in ("previous", "message", "is_preference", "direction", "strength", "target", "job_scoped"):
            assert mine[id_][key] == p[key], (id_, key)


def test_the_categories_cover_the_referents_the_issue_names_each_with_at_least_five():
    by = Counter(p["category"] for p in PAIRS)
    assert set(by) == set(ctx.CATEGORY_ORDER) == set(DOC["categories"])
    assert all(n >= 5 for n in by.values()), by
    # a referent that is a skill or an item, a section or a format, a one-off, a negated one, a misleading
    # turn that mentions several items, an irrelevant turn
    item = [p for p in PAIRS if p["category"] == "referent_item"]
    assert any((p["target"] or "").startswith(("skill:", "proj:")) for p in item) and any(p["target"] is None for p in item)
    fmt = [p for p in PAIRS if p["category"] == "referent_format"]
    assert {p["direction"] for p in fmt} >= {"format_rule", "emphasize"}
    assert all(not p["is_preference"] for p in PAIRS if p["category"] == "one_off")
    assert all(mg.prefilter(p["message"]).negated or "Don't" in p["message"] or "Never" in p["message"]
               or "keep it that way" in p["message"].lower() for p in PAIRS if p["category"] == "negated_referent")
    assert sum(p["direction"] == "emphasize" for p in PAIRS if p["category"] == "negated_referent") >= 4   # a negation that means keep
    for p in PAIRS:
        if p["category"] == "misleading_previous":
            catalog_names = [n for n in ("Excel", "Looker", "Tableau", "Python", "SQL", "Docker", "Git", "Spark",
                                         "Airflow", "dbt", "Pandas", "Matplotlib", "R") if n in p["previous"]]
            assert len(catalog_names) >= 2, p["id"]                         # the turn mentions several items
    assert sum(p["origin"] == "244" for p in PAIRS) >= 28


def test_labels_follow_202s_schema():
    keys = {c["key"] for c in base.profile_catalog(DOC["profile"])}
    assert (ROOT / "eval" / "profiles" / f"{DOC['profile']}.md").is_file()
    assert "personal" not in json.dumps([(p["message"], p["previous"], p["rationale"]) for p in PAIRS]).lower()
    for p in PAIRS:
        assert p["target"] is None or p["target"] in keys, (p["id"], p["target"])
        if not p["is_preference"]:
            assert (p["direction"], p["strength"], p["target"], p["job_scoped"]) == ("none", None, None, False), p["id"]
        else:
            assert p["direction"] in ("emphasize", "suppress", "format_rule") and p["strength"] in (1, 2, 3, 4, 5), p["id"]
            absolute = bool(ABSOLUTE.search(p["message"]))
            assert (p["strength"] == 5) == absolute, (p["id"], p["strength"])
    assert sum(p["is_preference"] for p in PAIRS) >= 30 and sum(not p["is_preference"] for p in PAIRS) >= 5


def test_the_set_leans_on_the_turn_so_the_detector_finds_a_reference_in_nearly_all():
    catalog = base.profile_catalog(DOC["profile"])
    missed = [p["id"] for p in PAIRS if not mg.unresolved_reference(p["message"], catalog)]
    assert set(missed) <= {"cx_three_bullets", "cx_irr_pdf"}, missed        # one with no cue, one that names its item


# ── the recordings ───────────────────────────────────────────────────────────

def test_every_pair_replays_alone_and_with_its_turn_with_no_live_call(replayed):
    rows, _, stats = replayed
    assert len(rows) == len(PAIRS)
    assert stats["hit_rate"] == 1.0 and stats["jev"] == 0 and stats["fallback"] == 0 and stats["requests"] == 0
    assert stats["cache"] == 4 * 2 * len(PAIRS)                              # two asks of four questions each
    assert all(r["v1"]["guess"].version == mg.VERSION and r["v2"]["guess"].version == mg.VERSION_V2 for r in rows)
    assert all(r["v1"]["guess"].source == r["v2"]["guess"].source == "cache" for r in rows)


def test_a_changed_turn_or_message_fails_loudly_instead_of_falling_back(isolated_engine, monkeypatch):
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    monkeypatch.setattr(engine, "get_client", lambda: pytest.fail("replay reached for a client"))
    recordings.import_recordings(PARENT)
    recordings.import_recordings(RECORDED)
    catalog = base.profile_catalog(DOC["profile"])
    p = PAIRS[13]
    assert all(a.source == "cache" for a in mg.ask(p["message"], catalog, p["previous"]))
    with pytest.raises(JevReplayMiss):
        mg.ask(p["message"], catalog, p["previous"] + " Extra.")
    with pytest.raises(JevReplayMiss):
        mg.ask(p["message"] + " Really.", catalog, p["previous"])


def test_the_recordings_are_v1_and_v2_answers_from_one_model_and_carry_no_text():
    assert {d["point"] for d in RECORDED["decisions"]} == {mg.POINT}
    assert {d["question_version"] for d in RECORDED["decisions"]} == {mg.VERSION, mg.VERSION_V2}
    assert {d["resolved_model"] for d in RECORDED["decisions"]} == {MODEL}
    blob = json.dumps(RECORDED["decisions"])
    assert not any(p["message"] in blob or p["previous"] in blob for p in PAIRS if len(p["message"]) > 20)
    assert not {d["cache_key"] for d in RECORDED["decisions"]} & {d["cache_key"] for d in PARENT["decisions"]}


def test_the_recordings_were_made_with_the_questions_the_gate_asks_today(isolated_engine):
    """A reworded v2 question changes the cache key: this is the test that says to re-record."""
    catalog = base.profile_catalog(DOC["profile"])
    keys = {d["cache_key"] for d in RECORDED["decisions"]}
    p = next(p for p in PAIRS if p["id"] == "cx_transit_never")
    for q in mg.questions_for(catalog, mg.VERSION_V2):
        assert cache.cache_key(mg.build_state_v2(p["message"], p["previous"]), q, MODEL) in keys
    new_v1 = next(p for p in PAIRS if p["origin"] == "244" and p["id"] == "cx_looker_stop")
    assert all(cache.cache_key(mg.build_state(new_v1["message"]), q, MODEL) in keys for q in mg.questions_for(catalog))


def test_the_main_sets_v1_recordings_still_answer_v1_exactly_as_before(isolated_engine, monkeypatch):
    """v1's questions, state and cache keys did not change, so every #202 answer still replays."""
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    monkeypatch.setattr(engine, "get_client", lambda: pytest.fail("replay reached for a client"))
    recordings.import_recordings(PARENT)
    catalog = base.profile_catalog(MAIN["profile"])
    for p in MAIN["pairs"][:12]:
        assert all(a.source == "cache" for a in mg.ask(p["message"], catalog))
    assert {d["question_version"] for d in PARENT["decisions"]} == {mg.VERSION}


# ── what v2 does with real answers ───────────────────────────────────────────

def test_v2_reads_the_context_messages_better_than_v1_on_standing_direction_and_target(replayed):
    rows, _, _ = replayed
    a, b = ctx.accuracy(ctx.v1_rows(rows)), ctx.accuracy(ctx.v2_rows(rows))
    assert b["standing"] > a["standing"] and b["recall"] > a["recall"]
    assert b["direction"] > a["direction"] and b["target"] > a["target"]
    assert b["standing"] >= 38 and b["target"] >= 26 and b["direction"] >= 30
    assert b["false_yes"] == 0 and a["false_yes"] == 0                         # the turn never makes a non-preference one
    # #202's own 8 labelled preferences: standing, direction and target all go up
    old = [r for r in rows if r["pair"]["origin"] == "202"]
    a8, b8 = ctx.accuracy(ctx.v1_rows(old)), ctx.accuracy(ctx.v2_rows(old))
    assert (a8["direction"], b8["direction"]) == (4, 8) and (a8["target"], b8["target"]) == (3, 6)
    assert b8["standing"] == 12


def test_a_misleading_or_irrelevant_turn_does_not_pull_the_answer_off_the_message(replayed):
    rows, _, _ = replayed
    for r in rows:
        if r["pair"]["category"] in ("misleading_previous", "irrelevant_previous"):
            assert r["v2"]["jev_pref"] == r["v2"]["is_preference"] or r["pair"]["id"] == "cx_irr_ok", r["pair"]["id"]
    # a named target the previous turn does not mention is still the named one
    by = {r["pair"]["id"]: r for r in rows}
    assert by["cx_irr_spark"]["v2"]["guess"].target_key == "skill:spark"            # the turn is about Excel
    assert by["cx_mis_airflow"]["v2"]["guess"].target_key == "skill:airflow"        # the turn lists Spark, Airflow and dbt
    assert by["cx_mis_second"]["v2"]["guess"].target_key == "skill:matplotlib"      # the turn lists Pandas and Matplotlib
    assert by["cx_irr_that"]["v2"]["guess"].target_key is None                       # nothing in the turn to bind


def test_a_wrong_write_never_happens_on_the_set_at_any_version_or_threshold(replayed):
    rows, _, _ = replayed
    for r in rows:
        for version_row in (r["v1"], r["v2"], r["shipped"]):
            assert base.route_row(version_row)["action"] != "write" or not base.write_is_wrong(
                version_row, base.route_row(version_row)), r["pair"]["id"]
    assert ctx.accuracy(ctx.shipped_rows(rows))["wrong"] == ctx.accuracy(ctx.v2_rows(rows))["wrong"] == 0
    # with every threshold open, what is written still names its item in the message, is under strength 5 and
    # stays out of a section: the code rules, not the thresholds, are what hold on this set
    for r in ctx.v2_rows(rows):
        for t in (0.0, 0.25, 0.5):
            d = base.route_row(r, 0.0, t, 0.0)
            if d["action"] == "write":
                assert r["guess"].target_named and d["guess"]["strength"] < 5 and not d["guess"]["target"].startswith("section:")
                assert not base.write_is_wrong(r, d), r["id"]


def test_a_target_only_the_previous_turn_names_is_never_written(replayed):
    """"Never list that again" binds to Transit Pulse under v2, and the message does not say it: the host decides."""
    rows, _, _ = replayed
    by = {r["pair"]["id"]: r for r in rows}
    r = by["cx_transit_never"]["v2"]
    assert r["guess"].target_key == "proj:transit pulse" and not r["guess"].target_named
    for t in (0.0, 0.5, 0.9):
        assert base.route_row(r, 0.0, t, 0.0)["action"] != "write"
    assert base.route_row(r)["action"] == "host"
    # every v2 binding the message does not name goes to the host, never to the store
    unnamed = [x for x in ctx.v2_rows(rows) if x["guess"].target_key and not x["guess"].target_named]
    assert len(unnamed) >= 20
    assert all(base.route_row(x, 0.0, 0.0, 0.0)["action"] != "write" for x in unnamed)


# ── the numbers v2 ships with ────────────────────────────────────────────────

def test_the_v2_constants_are_the_pinned_fit_and_v1s_are_unchanged(replayed):
    rows, _, _ = replayed
    rec = ctx.recommend(rows)
    assert (mg.TAU_LO_V2, mg.TAU_HI_V2, mg.TAU_TARGET_V2) == (rec["tau_lo"], rec["tau_hi"], rec["tau_target"])
    assert (mg.TAU_LO_V2, mg.TAU_HI_V2, mg.TAU_TARGET_V2) == (0.20, 0.65, 0.75)
    assert (mg.TAU_LO, mg.TAU_HI, mg.TAU_TARGET) == (0.25, 0.65, 0.75)               # #202's, unchanged
    assert mg.TAU_LO_V2 < mg.TAU_HI_V2 and 0 < mg.TAU_TARGET_V2 < 1
    # the fit alone, before the pin rule, is what #202's rules give on v2's answers
    assert rec["pinned"] and rec["raw"] == {"tau_hi": 0.50, "tau_target": 0.40, "tau_lo": 0.20}


def test_the_pin_rule_a_context_variant_is_never_looser_than_v1_while_its_write_set_is_thinner(replayed, monkeypatch):
    rows, _, _ = replayed
    rec = ctx.recommend(rows)
    assert rec["hi"]["autos"] == 3 < mg.V1_WOULD_BE_WRITES == 29 and rec["pinned"]
    assert mg.TAU_HI_V2 == max(rec["raw"]["tau_hi"], mg.TAU_HI) and mg.TAU_TARGET_V2 == max(rec["raw"]["tau_target"], mg.TAU_TARGET)
    assert mg.TAU_HI_V2 >= mg.TAU_HI and mg.TAU_TARGET_V2 >= mg.TAU_TARGET
    # TAU_LO is not a write threshold: it is fitted again under the pinned TAU_HI_V2, and still drops no preference
    assert rec["lo"]["dropped_prefs"] == 0 and rec["lo"]["tau_lo"] == mg.TAU_LO_V2
    # a set whose would-be writes reach v1's is fitted on its own evidence: the pin lets go
    monkeypatch.setattr(mg, "V1_WOULD_BE_WRITES", 3)
    free = ctx.recommend(rows)
    assert not free["pinned"] and (free["tau_hi"], free["tau_target"]) == (0.50, 0.40)
    # the pin never lowers a threshold the fit put above v1's
    monkeypatch.setattr(mg, "V1_WOULD_BE_WRITES", 29)
    monkeypatch.setattr(mg, "TAU_HI", 0.30)
    monkeypatch.setattr(mg, "TAU_TARGET", 0.20)
    again = ctx.recommend(rows)
    assert (again["tau_hi"], again["tau_target"]) == (0.50, 0.40)


def test_v1_would_be_writes_is_v1s_own_count_with_the_code_rules_off(isolated_engine, monkeypatch):
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    monkeypatch.setattr(engine, "get_client", lambda: pytest.fail("replay reached for a client"))
    recordings.import_recordings(PARENT)
    rows = base.replay_rows(MAIN)[0]
    off = base.fit_hi(rows, 0.0, mg.TAU_TARGET)
    bare = [r for r in rows if base.route_row(r, mg.TAU_LO, mg.TAU_HI, mg.TAU_TARGET, without=base.FIT_WITHOUT)["action"] == "write"]
    assert len(bare) == mg.V1_WOULD_BE_WRITES == 29 and off["tau_hi"] == mg.TAU_HI


def test_tau_hi_v2_fit_has_the_same_hard_floor_and_a_set_with_no_wrong_write_cannot_pull_it_under(replayed):
    rows, _, _ = replayed
    v2 = ctx.v2_rows(rows)
    assert mg.HI_FLOOR == 0.5 and mg.TAU_HI_V2 >= mg.HI_FLOOR
    hi = base.fit_hi(v2, 0.0, mg.TAU_TARGET_V2)
    assert min(hi["table"]) >= mg.HI_FLOOR and hi["tau_hi"] >= mg.HI_FLOOR
    assert hi["wrong"] == 0 and hi["autos"] == 3 and hi["danger_ids"] == [] and hi["kept_min"] == 0.84
    safe = [r for r in v2 if r["id"] not in hi["danger_ids"]]
    assert base.fit_hi(safe, 0.0, mg.TAU_TARGET_V2)["tau_hi"] >= mg.HI_FLOOR
    assert base.recommend(safe)["tau_hi"] >= mg.HI_FLOOR


def test_tau_hi_v2_is_fitted_as_if_the_code_rules_did_not_exist(replayed):
    rows, _, _ = replayed
    v2 = ctx.v2_rows(rows)
    assert set(base.FIT_WITHOUT) == {"hard", "section", "backstop"} and set(base.FIT_WITHOUT) <= set(mg.CODE_RULES)
    off = base.fit_hi(v2, 0.0, mg.TAU_TARGET_V2)
    assert off["without"] == list(base.FIT_WITHOUT) and off["tau_hi"] == 0.50       # the fit alone, before the pin
    # the rules remove writes, they never loosen the fit: with them on the gate writes nothing here, and the
    # fit is the same either way because no would-be wrong write exists
    on = base.fit_hi(v2, 0.0, mg.TAU_TARGET_V2, without=())
    assert on["autos"] == 0 and off["autos"] == 3 and on["tau_hi"] == mg.HI_FLOOR
    # a wrong write the rules would stop still sets the boundary: add one at strength 5, named, in a section
    row = dict(next(r for r in v2 if r["id"] == "cx_irr_spark"))
    row["is_preference"], row["id"] = False, "synthetic_wrong"
    assert "synthetic_wrong" in base.fit_hi(v2 + [row], 0.0, mg.TAU_TARGET_V2)["danger_ids"]


def test_tau_target_v2_fit_alone_has_no_wrong_named_binding_so_the_pin_decides(replayed):
    rows, _, _ = replayed
    v2 = ctx.v2_rows(rows)
    tg = base.fit_target(v2)
    assert tg["tau_target"] == 0.40 < mg.TAU_TARGET_V2 == 0.75                   # the rule alone is below any evidence
    assert (tg["binds"], tg["right"], tg["wrong"], tg["every"]) == (3, 3, 0, 28)
    assert tg["lowest_kept"] >= 0.8                                    # all three right named bindings score >= 0.83
    assert sorted(tg["unnamed_wrong_ids"]) == ["cx_ans_no_never", "cx_gpa_never", "cx_mis_last", "cx_past_tense",
                                               "cx_three_bullets"]     # wrong bindings only the name check holds back


def test_the_pinned_thresholds_leave_the_host_routing_and_the_writes_unchanged(replayed):
    rows, _, _ = replayed
    v2 = ctx.v2_rows(rows)
    pinned = {r["id"]: base.route_row(r) for r in v2}
    loose = {r["id"]: base.route_row(r, 0.20, 0.50, 0.50) for r in v2}
    assert not [i for i in pinned if pinned[i]["action"] == "write"] and not [i for i in loose if loose[i]["action"] == "write"]
    # the same action for every message (drop, host or write); only the reason a host hand-off carries can differ,
    # for the messages scored between the fitted 0.50 and the pinned 0.65 ("uncertain" in place of a rule's name)
    assert {i: d["action"] for i, d in pinned.items()} == {i: d["action"] for i, d in loose.items()}
    changed = [i for i in pinned if pinned[i]["reason"] != loose[i]["reason"]]
    assert all(0.5 <= next(r for r in v2 if r["id"] == i)["guess"].p < mg.TAU_HI_V2 for i in changed)
    assert changed == ["cx_ballot_drop", "cx_one_line_skills", "cx_edu_never_without", "cx_irr_cover", "cx_irr_that"]
    assert ctx.accuracy(v2)["wrong"] == 0 and ctx.accuracy(ctx.shipped_rows(rows))["wrong"] == 0
    assert ctx.accuracy(v2)["kept_prefs"] == 33


def test_tau_lo_v2_drops_no_true_preference(replayed):
    rows, _, _ = replayed
    v2 = ctx.v2_rows(rows)
    lo = base.fit_lo(v2, mg.TAU_HI_V2)
    assert lo["met"] and lo["dropped_prefs"] == 0 and lo["tau_lo"] == mg.TAU_LO_V2
    assert lo["lowest_pref"]["id"] == "cx_looker_stop" and round(lo["lowest_pref"]["guess"].p, 2) == 0.32
    reach = [r for r in v2 if base.reachable(r) and r["is_preference"]]
    assert all(base.route_row(r)["action"] != "drop" for r in reach)


def test_the_gate_with_v2_keeps_every_true_preference_for_the_host(replayed):
    rows, _, _ = replayed
    a, b = ctx.accuracy(ctx.v1_rows(rows)), ctx.accuracy(ctx.v2_rows(rows))
    assert b["kept_prefs"] == b["prefs"] == 33 and a["kept_prefs"] < b["kept_prefs"]


def test_detection_decides_what_ships_and_it_sends_the_turn_for_all_but_two(replayed):
    rows, catalog, _ = replayed
    det = ctx.detection(rows, base.load_pairs(), catalog)
    assert det["flagged"] >= 38 and set(det["missed"]) == {"cx_three_bullets", "cx_irr_pdf"}
    shipped = ctx.accuracy(ctx.shipped_rows(rows))
    assert shipped["standing"] >= 38 and shipped["wrong"] == 0
    assert det["main_flagged"] <= 10 and det["main_candidates"] <= 5


# ── the reports ──────────────────────────────────────────────────────────────

def test_the_committed_report_and_review_are_current(replayed):
    rows, catalog, stats = replayed
    meta = ctx._meta(RECORDED, PARENT, stats["hit_rate"], catalog)
    assert ctx.REPORT_PATH.read_text(encoding="utf-8") == ctx.render_report(rows, base.load_pairs(), catalog, meta) + "\n"
    assert ctx.REVIEW_PATH.read_text(encoding="utf-8") == ctx.render_review(rows) + "\n"


def test_the_review_lists_disagreements_first_then_every_message_with_its_turn():
    text = ctx.REVIEW_PATH.read_text(encoding="utf-8")
    assert text.index("## Disagreements with v2") < text.index("## Agreements")
    for p in PAIRS:
        assert f"`{p['id']}`" in text and p["previous"] in text
    assert "pending" not in text and "all 40 are user-confirmed" in text and "confirmed in #202" in text


def test_the_report_compares_v1_and_v2_on_standing_direction_target_and_writes():
    text = ctx.REPORT_PATH.read_text(encoding="utf-8")
    for needle in ("v1 alone", "v2 with the turn", "standing right", "direction right", "target right",
                   "automatic writes", "wrong automatic writes", "## Detection", "## The v2 thresholds"):
        assert needle in text, needle


def test_analyze_runs_offline_from_the_command_line(tmp_path):
    env = {k: v for k, v in __import__("os").environ.items() if k not in ("TYPESAFE_API_KEY", "DATABASE_URL")}
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.run([sys.executable, str(ROOT / "eval" / "fit_memory_gate_context.py"), "analyze",
                           "--report", str(tmp_path / "r.md"), "--review", str(tmp_path / "v.md")],
                          capture_output=True, text=True, cwd=ROOT, env=env, timeout=240)
    assert proc.returncode == 0, proc.stderr
    assert "hit rate 100%" in proc.stdout
    assert (tmp_path / "r.md").read_text(encoding="utf-8") == ctx.REPORT_PATH.read_text(encoding="utf-8")
