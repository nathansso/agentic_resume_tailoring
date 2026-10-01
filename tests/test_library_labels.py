"""The labelled sets for the bullet library's two Jev thresholds, and their recordings (issue #199).

`eval/library_labels/variants.json` (jobs asked about one item's approved variants) and
`baselines.json` (jobs asked against four saved tracks) are synthetic and labelled by hand;
`recordings.json` holds jev-1.13.0's real answers to `variant_choice@v1` and `track_baseline@v1`
for every case, recorded once. Nothing here calls the API: the key is removed from every test's
environment, and the replay tests fail on any miss instead of falling back.
"""

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from uuid import UUID

import pytest
from sqlmodel import Session

from eval import fit_library_thresholds as fit
from harness import library
from harness.decisions import cache, engine, library as jl, recordings
from harness.decisions.client import JevReplayMiss

ROOT = Path(__file__).resolve().parent.parent
VDOC, BDOC = fit.load_variants(), fit.load_baselines()
VCASES, BCASES = VDOC["cases"], BDOC["cases"]
RECORDED = json.loads(fit.RECORDINGS_PATH.read_text(encoding="utf-8"))
TRACKS = [t["track"] for t in BDOC["tracks"]]
NUMBER = re.compile(r"\d[\d,.]*")


@pytest.fixture()
def replayed(isolated_engine, monkeypatch):
    """Every case replayed once, offline, from the recordings: `(variant_rows, baseline_rows, stats)`."""
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    monkeypatch.setattr(engine, "get_client", lambda: pytest.fail("replay reached for a client"))
    recordings.import_recordings(RECORDED)
    return fit.replay_rows(VDOC, BDOC)


def _keys(text):
    """The content tokens `relevance_density` and the overlap fallback use (hyphenated words split)."""
    from agents.ats_scorer import ATSScoringEngine
    return ATSScoringEngine._extract_keywords(text.replace("-", " "))


# ── the variant set ──────────────────────────────────────────────────────────

def test_the_sets_are_versioned_commented_status_marked_and_large_enough():
    for doc in (VDOC, BDOC):
        assert doc["_comment"] and doc["version"] == 1
        assert doc["status"] == "planner-reviewed, pending user spot-check"
    assert len(VCASES) >= 40 and len(BCASES) >= 30
    assert len({c["id"] for c in VCASES}) == len(VCASES) and len({c["id"] for c in BCASES}) == len(BCASES)


def test_every_category_has_at_least_six_cases_in_both_sets():
    v, b = Counter(c["category"] for c in VCASES), Counter(c["category"] for c in BCASES)
    assert set(v) == set(VDOC["categories"]) == set(fit.VARIANT_CATEGORIES)
    assert set(b) == set(BDOC["categories"]) == set(fit.BASELINE_CATEGORIES)
    assert all(n >= 6 for n in v.values()), v
    assert all(n >= 6 for n in b.values()), b


def test_variant_cases_have_two_to_five_variants_and_a_single_variant_case_has_one():
    for c in VCASES:
        assert c["job"] in VDOC["jobs"] and c["item"] in VDOC["items"], c["id"]
        assert c["rationale"].strip(), c["id"]
        n = len(c["variants"])
        assert (n == 1) if c["category"] == "single" else (2 <= n <= 5), c["id"]
        assert set(c["best"]) <= set(c["variants"]) and set(c["poor_fit"]) <= set(c["variants"]), c["id"]
        assert not set(c["best"]) & set(c["poor_fit"]), c["id"]
        assert set(c["variants"]) <= set(VDOC["items"][c["item"]]["variants"]), c["id"]


def test_the_labels_mean_what_the_categories_say():
    by = {cat: [c for c in VCASES if c["category"] == cat] for cat in VDOC["categories"]}
    assert all(len(c["best"]) == 1 for c in by["clear_best"])
    assert all(len(c["best"]) >= 2 for c in by["close_call"])
    for c in by["no_match"]:
        assert c["best"] == [] and set(c["poor_fit"]) == set(c["variants"]), c["id"]
    assert all(c["best"] for c in by["synonym"])
    singles = by["single"]
    assert sum(bool(c["best"]) for c in singles) >= 4 and sum(not c["best"] for c in singles) >= 4
    assert sum(not c["best"] for c in by["keyword_trap"]) >= 2          # a trap with nothing right to pick


def test_a_synonym_variant_shares_no_term_with_the_job_and_the_other_variants_do():
    """The synonym category's point: the fitting variant says it in other words, so word overlap cannot
    find it. Every case has one such variant (`_syn`), labelled best."""
    for c in (c for c in VCASES if c["category"] == "synonym"):
        job = VDOC["jobs"][c["job"]]
        job_terms = _keys(job["title"] + " " + " ".join(r["text"] for r in job["requirements"]))
        item = VDOC["items"][c["item"]]
        (syn,) = [v for v in c["variants"] if v.endswith("_syn")]
        assert syn in c["best"] and not _keys(item["variants"][syn]["text"]) & job_terms, c["id"]


def test_a_keyword_trap_variant_repeats_the_postings_words():
    """In the trap category the wrong variant (labelled poor fit, or the only one on offer) shares at
    least one term with the job, so word overlap is drawn to it."""
    traps = [c for c in VCASES if c["category"] == "keyword_trap"]
    drawn = []
    for c in traps:
        job = VDOC["jobs"][c["job"]]
        job_terms = _keys(job["title"] + " " + " ".join(r["text"] for r in job["requirements"]))
        item = VDOC["items"][c["item"]]
        wrong = [v for v in c["variants"] if v not in c["best"]]
        if any(_keys(item["variants"][v]["text"]) & job_terms for v in wrong):
            drawn.append(c["id"])
    # `e_esri_go` echoes "process large datasets" with "processes 200k records": by meaning, not by token
    assert len(drawn) >= len(traps) - 1, drawn


def test_every_variant_is_a_faithful_phrasing_of_a_bullet_in_a_synthetic_profile():
    """The base bullet is verbatim in the profile's item, the variant adds no number the base lacks, and
    the two share most of their words. Profiles are eval/profiles, never personal/."""
    from eval.profile_fixture import load_profile

    for ikey, item in VDOC["items"].items():
        path = fit.PROFILES / f"{item['profile']}.md"
        assert path.is_file() and "personal" not in path.parts
        prof = load_profile(path)
        if item["kind"] == "experience":
            found = next(e for e in prof.experiences if f"{e['title']} @ {e['company']}" == item["title"])
        else:
            found = next(p for p in prof.projects if p["name"] == item["title"])
        assert found["bullets"] == item["bullets"], ikey
        for vkey, v in item["variants"].items():
            assert v["base"] in item["bullets"], (ikey, vkey)
            assert set(NUMBER.findall(v["text"])) <= set(NUMBER.findall(v["base"])), (ikey, vkey)
            shared = _keys(v["text"]) & _keys(v["base"])
            assert len(shared) / max(len(_keys(v["base"])), 1) >= 0.25, (ikey, vkey)


def test_jobs_carry_a_title_and_posting_style_requirements():
    for key, job in VDOC["jobs"].items():
        assert job["title"].strip() and 5 <= len(job["requirements"]) <= 8, key
        assert all(r["text"].strip() and r["type"] in ("required", "preferred") for r in job["requirements"]), key
    for c in BCASES:
        assert 5 <= len(c["requirements"]) <= 8 and c["rationale"].strip(), c["id"]


# ── the baseline set ─────────────────────────────────────────────────────────

def test_baseline_labels_are_tracks_or_none_and_mean_what_the_categories_say():
    assert 3 <= len(TRACKS) <= 4 and all(t["title"].strip() for t in BDOC["tracks"])
    assert set(TRACKS) <= set(library.ROLE_FAMILIES)                  # so the #229 lookup can ever match
    for c in BCASES:
        assert set(c["best"]) <= set(TRACKS), c["id"]
        if c["category"] in ("no_track", "near_miss"):
            assert c["best"] == [], c["id"]
        else:
            assert 1 <= len(c["best"]) <= 2, c["id"]
        if c["category"] == "hybrid_title":
            assert len(c["best"]) == 2, c["id"]
        if c["category"] == "clear_match":
            assert len(c["best"]) == 1 and fit.fallback_baseline(BDOC, c) == c["best"][0], c["id"]


def test_a_title_mismatch_is_one_the_title_lookup_gets_wrong():
    """The point of the category: the title says one thing and the duties another, so #229's lookup
    (which sees the title only) lands on another track, or none."""
    for c in (c for c in BCASES if c["category"] == "title_mismatch"):
        assert fit.fallback_baseline(BDOC, c) not in c["best"], c["id"]


# ── replaying the recordings ─────────────────────────────────────────────────

def test_every_case_replays_from_the_recordings_with_no_live_call(replayed):
    vrows, brows, stats = replayed
    assert len(vrows) == len(VCASES) and len(brows) == len(BCASES)
    for point, n in ((jl.VARIANT_POINT, len(VCASES)), (jl.BASELINE_POINT, len(BCASES))):
        assert stats[point]["hit_rate"] == 1.0 and stats[point]["cache"] == n
        assert stats[point]["jev"] == 0 and stats[point]["fallback"] == 0 and stats[point]["requests"] == 0
    assert all(r["source"] == "cache" and 0.0 <= (r["p"] or 0.0) <= 1.0 for r in vrows + brows)


def test_a_changed_case_fails_loudly_instead_of_falling_back(isolated_engine, monkeypatch):
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    monkeypatch.setattr(engine, "get_client", lambda: pytest.fail("replay reached for a client"))
    recordings.import_recordings(RECORDED)
    assert fit.ask_variant_case(VDOC, VCASES[0]).source == "cache"
    changed = json.loads(json.dumps(VDOC))
    changed["jobs"][VCASES[0]["job"]]["requirements"][0]["text"] += " and more"
    with pytest.raises(JevReplayMiss):
        fit.ask_variant_case(changed, VCASES[0])
    other = json.loads(json.dumps(BDOC))
    other["tracks"][0]["title"] = "Something Else"
    with pytest.raises(JevReplayMiss):
        fit.ask_baseline_case(other, BCASES[0])


def test_the_recordings_carry_no_job_state_and_were_made_with_the_questions_asked_today():
    blob = json.dumps(RECORDED["decisions"])
    assert not any(r["text"] in blob for j in VDOC["jobs"].values() for r in j["requirements"])
    assert {d["point"] for d in RECORDED["decisions"]} == {jl.VARIANT_POINT, jl.BASELINE_POINT}
    assert {d["question_version"] for d in RECORDED["decisions"]} == {jl.VARIANT_VERSION, jl.BASELINE_VERSION}
    assert {d["resolved_model"] for d in RECORDED["decisions"]} == {"jev-1.13.0"}
    assert len(RECORDED["decisions"]) == len(VCASES) + len(BCASES)


def test_a_reworded_question_changes_every_key_this_is_the_test_that_says_to_re_record():
    keys = {d["cache_key"] for d in RECORDED["decisions"]}
    for c in VCASES:
        title, reqs, item_title, variants = fit.variant_inputs(VDOC, c)
        q = jl.variant_question(variants)
        assert cache.cache_key(jl.variant_state(title, reqs, item_title), q, "jev-1.13.0") in keys, c["id"]
    for c in BCASES:
        q, _ = jl.track_question(fit.track_rows(BDOC))
        assert cache.cache_key(jl.baseline_state(c["title"], c["requirements"]), q, "jev-1.13.0") in keys, c["id"]


# ── the product path asks exactly what was recorded ──────────────────────────

def _seed_synthetic_user(profile_slug):
    from eval.profile_fixture import load_profile
    from eval.scripted_host import seed_profile

    return seed_profile(load_profile(fit.PROFILES / f"{profile_slug}.md"))


def test_suggest_actions_asks_the_question_that_was_recorded_and_reads_jevs_answer(replayed_store, monkeypatch):
    """A user holding the recorded item, with the recorded variants (their ids), opens the recorded job: the
    state and question `suggest_actions` builds hash to the recorded key, so the answer is Jev's own."""
    from database.models import BulletVariant
    from harness.contract import invoke

    case = next(c for c in VCASES if c["id"] == "a_esri_churn")
    job, item = VDOC["jobs"][case["job"]], VDOC["items"][case["item"]]
    uid = _seed_synthetic_user(item["profile"])
    item_key = next(i["key"] for i in invoke("list_items", uid, {})["items"]
                    if i["kind"] == "experience" and "fairhaven" in i["key"])
    with Session(replayed_store) as s:
        for k in case["variants"]:
            s.add(BulletVariant(variant_id=UUID(fit.variant_id(VDOC, k)), user_id=uid, item_key=item_key,
                                text=item["variants"][k]["text"], status="approved", cites=[item_key], tags={}))
        s.commit()
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    opened = invoke("open_job", uid, {"jd_text": "Posting text that is never sent to Jev.",
                                      "requirements": job["requirements"],
                                      "metadata": {"title": job["title"], "company": "Acme"}})
    out = {i["item_key"]: i for i in invoke("suggest_actions", uid, {"job_id": opened["job_id"]})["items"]}
    got = out[item_key]
    assert got["source"] == "cache" and got["match"] == "variant"
    assert got["variant"]["variant_id"] == fit.variant_id(VDOC, "f_churn")
    assert got["propensity"][fit.variant_id(VDOC, "f_churn")] == pytest.approx(0.97, abs=0.005)
    assert set(got["propensity"]) == {*(fit.variant_id(VDOC, k) for k in case["variants"]), "no_match"}


def test_open_job_asks_the_question_that_was_recorded_and_reads_jevs_answer(replayed_store, monkeypatch):
    from harness.contract import invoke

    case = next(c for c in BCASES if c["id"] == "m_swe_models")        # title says software engineer, duties ML
    uid = _seed_synthetic_user("data_science_generalist_danielle_okafor")
    for t in BDOC["tracks"]:                                          # one saved track per recorded track
        opened = invoke("open_job", uid, {"jd_text": f"{t['title']} role.",
                                          "metadata": {"title": t["title"], "company": f"{t['track']} Co"}})
        node = _run_empty(uid, opened["job_id"])
        assert invoke("save_baseline", uid, {"node_id": node, "track": t["track"]})["track"] == t["track"]
    monkeypatch.setenv("ART_JEV_MODE", "replay")                      # setup ran with Jev off
    out = invoke("open_job", uid, {"jd_text": "Posting text that is never sent to Jev.",
                                   "requirements": case["requirements"],
                                   "metadata": {"title": case["title"], "company": "Acme"}})
    assert out["role_family"] == "software_engineering"               # the lookup would start from the wrong track
    assert out["baseline"]["track"] == "machine_learning" and out["baseline"]["source"] == "cache"
    assert out["baseline"]["p"] == pytest.approx(0.93, abs=0.005)


@pytest.fixture()
def replayed_store(isolated_engine, monkeypatch):
    """A store holding the recordings, with Jev off (the suite's default): a test turns replay on when
    its setup is done, so setup never asks."""
    recordings.import_recordings(RECORDED)
    monkeypatch.setattr(engine, "get_client", lambda: pytest.fail("replay reached for a client"))
    return isolated_engine


def _run_empty(uid, job_id):
    from harness.executor import execute_plan
    out = execute_plan(uid, {"job_id": job_id, "nodes": []})
    assert out["committed"], out
    return out["node_id"]


# ── the numbers the library ships with ───────────────────────────────────────

def test_the_constants_in_library_equal_the_fit(replayed):
    vrows, brows, _ = replayed
    rec = fit.recommend(vrows, brows)
    assert (jl.TAU_VARIANT, jl.TAU_BASELINE) == (rec["tau_variant"], rec["tau_baseline"])
    assert (jl.TAU_VARIANT, jl.TAU_BASELINE) == (0.50, 0.50)


def test_both_thresholds_have_a_hard_floor_of_one_half(replayed):
    vrows, brows, _ = replayed
    assert jl.TAU_FLOOR == 0.5 and fit.GRID[0] == jl.TAU_FLOOR
    assert jl.TAU_VARIANT >= jl.TAU_FLOOR and jl.TAU_BASELINE >= jl.TAU_FLOOR
    assert min(fit.fit_variant(vrows)["table"]) >= jl.TAU_FLOOR
    assert min(fit.fit_baseline(brows)["table"]) >= jl.TAU_FLOOR
    # a set in which Jev is never wrong cannot pull a threshold under the floor
    safe_v = [r for r in vrows if fit.variant_outcome(r, r["jev_pick"]) != "poor"]
    safe_b = [r for r in brows if fit.baseline_outcome(r, r["jev_pick"]) != "wrong"]
    assert fit.fit_variant(safe_v)["tau"] >= jl.TAU_FLOOR and fit.fit_baseline(safe_b)["tau"] >= jl.TAU_FLOOR


def test_the_fit_sees_jevs_raw_answer_alone_as_if_no_code_rule_existed(replayed):
    """The host's explicit role family, the fallbacks and the drift guard are code rules: the fit must not
    depend on them, so changing what they would have said changes nothing."""
    vrows, brows, _ = replayed
    base = fit.recommend(vrows, brows)
    stripped_v = [{**r, "fallback_pick": None} for r in vrows]
    stripped_b = [{**r, "fallback_pick": None, "title": "Anything"} for r in brows]
    again = fit.recommend(stripped_v, stripped_b)
    assert (again["tau_variant"], again["tau_baseline"]) == (base["tau_variant"], base["tau_baseline"])


def test_the_rules_are_no_bad_pick_first_then_the_most_right_picks_then_the_middle_of_the_gap():
    def row(i, pick, p, best=("a",), poor=("b",)):
        return {"id": i, "jev_pick": pick, "p": p, "best": list(best), "poor_fit": list(poor)}

    # a poor-fit pick at 0.80 forces the threshold above it, at the cost of right picks under it
    rows = [row("r1", "a", 0.95), row("r2", "a", 0.70), row("bad", "b", 0.80), row("r3", "a", 0.55)]
    fit_v = fit.fit_variant(rows)
    assert fit_v["bad"] == 0 and fit_v["right"] == 1
    assert fit_v["danger_id"] == "bad" and fit_v["candidates"] == [0.85, 0.9, 0.95]
    # the middle of the gap between the worst bad pick (0.80) and the lowest right pick kept (0.95) is
    # 0.875, equally near 0.85 and 0.90: ties go to the higher
    assert fit_v["kept_min"] == 0.95 and fit_v["tau"] == 0.9
    # with no bad pick anywhere the floor binds, whatever the right picks score
    clean = [row("r1", "a", 0.95), row("r2", "a", 0.90)]
    assert fit.fit_variant(clean)["tau"] == 0.5
    # a baseline pick for a job no track fits is wrong at any p
    brow = {"id": "none_job", "jev_pick": "t", "p": 0.60, "best": [], "poor_fit": []}
    assert fit.baseline_outcome(brow, "t") == "wrong"
    assert fit.fit_baseline([brow, row("ok", "a", 0.9)])["tau"] == 0.75
    # the middle of the gap, when it falls on a grid value
    mid = [row("good", "a", 0.90), row("bad", "b", 0.70)]
    assert fit.fit_variant(mid)["tau"] == 0.8


def test_jev_picks_no_poor_fit_variant_and_no_wrong_track_at_the_shipped_thresholds(replayed):
    vrows, brows, _ = replayed
    v = fit.tally(vrows, fit.variant_outcome, lambda r: fit.picked(r, jl.TAU_VARIANT))
    b = fit.tally(brows, fit.baseline_outcome, lambda r: fit.picked(r, jl.TAU_BASELINE))
    assert v["poor"] == 0 and b["wrong"] == 0
    assert v["picks"] == 25 and v["right_picks"] == 24 and v["correct"] == 39
    assert b["picks"] == 21 and b["right_picks"] == 21 and b["correct"] == 35
    # Jev's own answer, with no threshold
    assert fit.tally(vrows, fit.variant_outcome, lambda r: r["jev_pick"])["poor"] == 0
    assert fit.tally(brows, fit.baseline_outcome, lambda r: r["jev_pick"])["wrong"] == 0


def test_jev_beats_the_fallbacks_on_the_same_cases(replayed):
    vrows, brows, _ = replayed
    v_jev = fit.tally(vrows, fit.variant_outcome, lambda r: fit.picked(r, jl.TAU_VARIANT))
    v_fb = fit.tally(vrows, fit.variant_outcome, lambda r: r["fallback_pick"])
    b_jev = fit.tally(brows, fit.baseline_outcome, lambda r: fit.picked(r, jl.TAU_BASELINE))
    b_fb = fit.tally(brows, fit.baseline_outcome, lambda r: r["fallback_pick"])
    assert (v_jev["correct"], v_fb["correct"]) == (39, 18) and v_fb["poor"] == 7
    assert (b_jev["correct"], b_fb["correct"]) == (35, 27) and b_fb["wrong"] == 8
    # the synonym and title-mismatch categories are where word overlap and the title lookup fail
    syn = [r for r in vrows if r["category"] == "synonym"]
    assert fit.tally(syn, fit.variant_outcome, lambda r: r["fallback_pick"])["correct"] == 0
    mis = [r for r in brows if r["category"] == "title_mismatch"]
    assert fit.tally(mis, fit.baseline_outcome, lambda r: r["fallback_pick"])["correct"] == 0
    assert fit.tally(mis, fit.baseline_outcome, lambda r: r["jev_pick"])["correct"] == len(mis)


def test_the_disagreements_are_the_four_variant_cases_and_none_moves_a_threshold(replayed):
    vrows, brows, _ = replayed
    dis = fit.disagreements(vrows, fit.variant_outcome)
    assert {r["id"] for r in dis} == {"a_mg_dashboard", "d_next_alerts", "d_tebra_backfill", "d_rbi_scripted_env"}
    assert fit.disagreements(brows, fit.baseline_outcome) == []
    # none is a poor-fit pick (which is what sets TAU_VARIANT), so no relabel in Jev's favour moves it
    assert all(fit.variant_outcome(r, r["jev_pick"]) in ("lost", "wrong") for r in dis)
    without = [r for r in vrows if r["id"] not in {d["id"] for d in dis}]
    assert fit.fit_variant(without)["tau"] == jl.TAU_VARIANT


def test_the_alternative_rule_is_reported_and_gets_the_close_calls_the_shipped_rule_loses(replayed):
    vrows, _, _ = replayed
    close = [r for r in vrows if r["category"] == "close_call"]
    shipped = fit.tally(close, fit.variant_outcome, lambda r: fit.picked(r, jl.TAU_VARIANT))["correct"]
    alt = fit.tally(close, fit.variant_outcome, lambda r: fit.alt_variant_pick(r, 0.5))
    assert alt["correct"] > shipped and alt["poor"] == 0


# ── the reports ──────────────────────────────────────────────────────────────

def test_the_committed_report_and_review_are_current(replayed):
    vrows, brows, _ = replayed
    assert fit.REPORT_PATH.read_text(encoding="utf-8") == fit.render_report(vrows, brows, fit._meta(RECORDED, 1.0)) + "\n"
    assert fit.REVIEW_PATH.read_text(encoding="utf-8") == fit.render_review(vrows, brows) + "\n"


def test_the_review_lists_disagreements_first_and_every_case_with_its_status(replayed):
    text = fit.REVIEW_PATH.read_text(encoding="utf-8")
    assert text.index("## Variant disagreements") < text.index("## Baseline disagreements") \
        < text.index("## Variant agreements") < text.index("## Baseline agreements")
    assert "planner-reviewed, pending user spot-check" in text
    for c in VCASES + BCASES:
        assert f"`{c['id']}`" in text


def test_analyze_runs_offline_from_the_command_line(tmp_path):
    env = {k: v for k, v in __import__("os").environ.items() if k not in ("TYPESAFE_API_KEY", "DATABASE_URL")}
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.run([sys.executable, str(ROOT / "eval" / "fit_library_thresholds.py"), "analyze",
                           "--report", str(tmp_path / "r.md"), "--review", str(tmp_path / "v.md")],
                          capture_output=True, text=True, cwd=ROOT, env=env, timeout=240)
    assert proc.returncode == 0, proc.stderr
    assert "hit rate 100%" in proc.stdout
    assert (tmp_path / "r.md").read_text(encoding="utf-8") == fit.REPORT_PATH.read_text(encoding="utf-8")
