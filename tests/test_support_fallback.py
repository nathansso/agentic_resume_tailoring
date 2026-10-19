"""The cited-bullet support check without Jev (issue #351).

With no key, `ART_JEV_MODE=off` or an API error Jev cannot answer, and before #351 the bullet went
unchecked. `harness/decisions/support_fallback.py` reads it instead: small explicit word lists for role
inflation, invented scope and contradictions, scored as a sum of signal weights, with two cutoffs fitted
on `eval/support_labels/pairs.json` (`eval/fit_support_fallback.py`, recorded in `harness/policy.json`).

Three kinds of test live here:

- the labelled-set figures (the set the lists were authored on, so a regression floor and not an accuracy);
- bullets written after the lists, honest and dishonest, that were never in the labelled set;
- the real executor, with no key, reverting an unsupported bullet and keeping an honest one.

Nothing here uses a key, a recording of a live call or the network.
"""

import json
import re
from pathlib import Path

import pytest

from eval import fit_support_fallback as fit
from harness import policy
from harness.decisions import engine, recordings, support, support_fallback as sf
from harness.decisions.support import reviews, violations
from test_executor import _run, _strip, env  # noqa: F401  (fixture)
from test_jev import Boom, auto  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent
PAIRS = fit.load_pairs()
ROWS = fit.run_pairs(PAIRS)
BY_ID = {r["id"]: r for r in ROWS}


def block(r):
    return r["score"] >= sf.TAU_BLOCK


def review(r):
    return sf.TAU_REVIEW <= r["score"] < sf.TAU_BLOCK


# ── the labelled set: the bar from eval/FALLBACKS.md ─────────────────────────

def test_no_supported_pair_is_blocked_or_reviewed():
    """The hard bar: 0 of the 35 supported pairs blocked by the new check, and at most 3 surfaced for review."""
    supported = [r for r in ROWS if r["label"] == "supported"]
    assert len(supported) == 35
    assert [r["id"] for r in supported if block(r)] == []
    assert sum(review(r) for r in supported) <= 3
    assert sum(review(r) for r in supported) == 0 and max(r["score"] for r in supported) == 0


def test_the_main_figure_is_met_with_the_123_gate_counted():
    should = [r for r in ROWS if r["label"] != "supported"]
    assert len(should) == 54
    check = sum(block(r) for r in should)
    either = sum(block(r) or r["g123"] for r in should)
    assert (check, either) == (36, 47)
    assert either >= 35
    for label, floor_ in (("contradicts", 10), ("adds_unsupported", 25)):
        assert sum((block(r) or r["g123"]) for r in should if r["label"] == label) >= floor_


def test_every_category_is_read_and_the_ones_the_issue_named_are_caught():
    by_cat = {}
    for r in ROWS:
        if r["label"] != "supported":
            by_cat.setdefault(r["category"], []).append(r)
    caught = {c: sum(block(r) for r in rs) for c, rs in by_cat.items()}
    assert caught["role_inflation"] == 11 == len(by_cat["role_inflation"])        # all of #350's 11
    assert caught["invented_scope_outcome"] >= 8                                   # of 13; #123 holds 2
    assert caught["contradiction"] >= 9 and caught["boundary"] >= 3
    assert sum(r["g123"] for r in by_cat["role_inflation"]) == 0                  # none of them was #123's


@pytest.mark.parametrize("pair_id", [
    "infl_maintained_owned", "infl_reviewed_led", "infl_compared_architected", "infl_wrote_designed",
    "infl_implemented_owner", "scope_production_org", "scope_whole_corpus", "scope_high_availability",
    "scope_edge_fleet", "scope_outperforms", "scope_invented_impact", "scope_deployed_replaced",
    "contra_rewrites_draft", "contra_flat_significant", "contra_image_text", "contra_offline_remote",
    "contra_cron_direction", "contra_wrote_reviewed", "bd_hundreds_of_runs", "bd_deep_learning_sklearn",
    "bd_specific_algorithm", "tool_kubernetes", "tool_xgboost", "tool_langchain_added"])
def test_these_should_block_pairs_are_blocked(pair_id):
    r = BY_ID[pair_id]
    assert block(r), (pair_id, r["score"], r["signals"])
    assert r["fb_label"] == r["label"] or r["label"] == "adds_unsupported" or r["fb_label"] == "contradicts"


def test_what_the_lists_cannot_read_passes_and_is_reported():
    """Known misses, named: a dropped qualifier, an added clause in plain words, an effect antonym and an
    original that is not evidence. A word list cannot read them; Jev does (51 of 54)."""
    silent = {r["id"] for r in ROWS if r["label"] != "supported" and not (block(r) or r["g123"] or review(r))}
    assert silent == {"rw_dependency_free", "scope_no_internet", "scope_plagiarism_feature", "scope_hourly_refresh",
                      "contra_replay_overwrites", "bd_original_not_evidence"}


# ── bullets written after the lists: honest ones pass, dishonest ones do not ──

HONEST = [
    # (evidence, bullet) - rewordings of the same facts, including the words the lists watch
    (["Led a team of 4 interns building a dashboard."], "Led four interns on a dashboard build."),
    (["Built a Flask API serving 3 internal tools."], "Developed a Flask API that serves three internal tools."),
    (["Replaced a manual spreadsheet process with a Python script."],
     "Automated a manual spreadsheet workflow with a Python script, eliminating hand entry."),
    (["Migrated the nightly job from cron to Airflow."], "Moved the nightly job from cron to Airflow."),
    (["Reduced query latency by 35% with an index."], "Cut query latency by 35% using an index."),
    (["Improved model recall by 9 points."], "Raised model recall by 9 points."),
    (["Trained a CNN image classifier on 12k photos."], "Trained a convolutional image classifier on 12k photos."),
    (["Wrote unit tests without mocking the database."],
     "Wrote unit tests that run against a real database instead of mocks."),
    (["Maintained the deployment scripts for the lab cluster."], "Managed deployment scripts for the lab cluster."),
    (["Designed and built a REST API for the clinic."], "Designed a REST API for the clinic."),
    (["Implemented an LSTM forecaster in PyTorch."], "Built a deep learning forecaster with torch."),
    (["Deployed the service to Kubernetes with Helm."], "Shipped the service on Kubernetes."),
    (["Trained classifiers for churn."], "Trained a supervised churn classifier."),
    (["Reduced 200 features to 12 with PCA."], "Applied principal component analysis to reduce 200 features to 12."),
    (["Built Airflow DAGs for the weekly load."], "Built Airflow directed acyclic graphs for the weekly load."),
    (["Tracked 60 experiment runs in MLflow."], "Tracked dozens of experiment runs in MLflow."),
    (["Ran the lab's weekly office hours for 30 students."], "Ran weekly office hours for 30 students in the lab."),
    (["Cut cloud spend 20% by right-sizing instances."], "Reduced cloud spend by 20% through instance right-sizing."),
    (["Used pandas to clean 9 semesters of enrollment data."], "Cleaned nine semesters of enrollment data with pandas."),
    (["Founded the campus data club and ran its first workshop."], "Started the campus data club and ran its first workshop."),
]

DISHONEST = [
    (["Helped maintain the CI pipeline."], "Led the CI pipeline migration.", "role"),
    (["Contributed to a Flask API."], "Managed a Flask API.", "role"),
    (["Built a tool for 3 analysts."], "Built an enterprise-wide tool for analysts.", "scope"),
    (["Cut cloud spend 20%."], "Increased cloud spend 20%.", "contradiction"),
    (["Migrated reports from Excel to Tableau."], "Migrated reports from Tableau back to Excel.", "contradiction"),
    (["Processed 40 files."], "Processed thousands of files.", "quantity"),
    (["Wrote a script that runs offline."], "Wrote a script that calls a hosted API.", "contradiction"),
    (["Trained a text classifier on 5k reviews."], "Trained an image classifier on 5k reviews.", "contradiction"),
    (["Built a model that works without labels."], "Built a model that labels data with regression models.", None),
    (["Wrote a parser for the build logs."], "Architected the build-log parsing platform.", "role"),
    (["Fit a model in scikit-learn."], "Fit a model with a deep learning approach in scikit-learn.", "method"),
    (["Built a service in Go."], "Built a service in Go with fault tolerance and zero downtime.", "scope"),
    (["Ran a pilot with 12 users."], "Ran a pilot that outperformed the legacy tool.", "scope"),
    (["Wrote the signup form."], "Wrote the signup form, written by another engineer.", "contradiction"),
    (["Built a scraper in Python."], "Built a scraper in Python with plotly dashboards.", "tool"),
]


@pytest.mark.parametrize("evidence,bullet", HONEST)
def test_an_honest_reword_passes_with_no_review(evidence, bullet):
    a = sf.assess(bullet, evidence)
    assert a["score"] == 0 and a["label"] == "supported" and a["signals"] == [], [s.text() for s in a["signals"]]


@pytest.mark.parametrize("evidence,bullet,kind", [d for d in DISHONEST if d[2]])
def test_a_dishonest_reword_is_blocked_by_a_named_signal(evidence, bullet, kind):
    a = sf.assess(bullet, evidence)
    assert a["score"] >= sf.TAU_BLOCK, [s.text() for s in a["signals"]]
    assert kind in {s.kind for s in a["signals"]}
    assert a["label"] == ("contradicts" if kind == "contradiction" else "adds_unsupported") or kind == "quantity"


def test_a_polarity_flip_is_a_contradiction_only_when_the_bullet_has_no_negation_of_its_own():
    ev = ["Built an assistant that suggests edits without rewriting a student's draft."]
    assert sf.assess("Built an assistant that rewrites students' drafts.", ev)["label"] == "contradicts"
    assert sf.assess("Built an assistant that suggests edits and never rewrites a draft.", ev)["score"] == 0
    assert sf.assess("Built an assistant that suggests edits, leaving the draft untouched.", ev)["score"] == 0


def test_the_verbs_only_reverse_on_the_same_object():
    ev = ["Raised ranking precision by 8 points."]
    assert sf.assess("Cut ranking latency by 8 points.", ev)["score"] == 0          # a different object
    assert sf.assess("Cut ranking precision by 8 points.", ev)["label"] == "contradicts"


# ── what is soft, what is strong, and what the original changes ──────────────

def test_a_soft_signal_reviews_and_two_block_and_a_strong_one_blocks():
    one = sf.assess("Built the report on a shared platform.", ["Built the report."])
    assert (one["score"], [s.text() for s in one["signals"]]) == (0.5, ["role: platform"])
    two = sf.assess("Built the reporting platform and its infrastructure.", ["Built the report."])
    assert two["score"] == 1.0
    assert sf.assess("Owned the report.", ["Built the report."])["score"] == 1.0
    assert sf.TAU_REVIEW <= one["score"] < sf.TAU_BLOCK <= two["score"]


def test_a_helper_verb_in_the_evidence_makes_a_soft_stretch_strong():
    assert sf.assess("Managed the nightly job.", ["Helped with the nightly job."])["score"] == 1.0
    assert sf.assess("Managed the nightly job.", ["Ran the nightly job."])["score"] == 0


def test_a_word_the_original_already_carried_counts_for_half():
    ev, bullet = ["Wrote the ingest job."], "Owned the ingest job."
    assert sf.assess(bullet, ev)["score"] == 1.0
    assert sf.assess(bullet, ev, original="Owned the ingest job for the team.")["score"] == 0.5     # a review, not a block
    assert sf.assess(bullet, ev, original="Owned the ingest job for the team.")["label"] == "adds_unsupported"


def test_the_rest_of_the_item_is_context_that_can_only_support():
    assert sf.assess("Led the migration.", ["Wrote the migration plan."])["score"] == 1.0
    assert sf.assess("Led the migration.", ["Wrote the migration plan."], context=["Senior Engineer", "Led the audit."])["score"] == 0
    # a contradiction is judged against the cited evidence; context never creates one
    assert sf.assess("Wrote a text classifier.", ["Wrote an image classifier."], context=["Wrote a text classifier."])["score"] == 0


def test_lead_is_a_verb_only_where_a_verb_goes():
    ev = ["Built the funnel dashboard."]
    assert sf.assess("Lead generation dashboard for the funnel.", ev)["score"] == 0
    assert sf.assess("Lead the funnel dashboard build.", ev)["score"] == 1.0
    assert sf.assess("Built the dashboard and lead its rollout.", ev)["score"] == 1.0


def test_an_alias_is_the_same_tool():
    assert sf.assess("Trained models with PyTorch.", ["Trained models with torch."])["score"] == 0
    assert sf.assess("Trained models with scikit-learn.", ["Trained models with sklearn."])["score"] == 0
    a = sf.assess("Trained models with spacy.", ["Trained models with torch."])
    assert [s.text() for s in a["signals"]] == ["tool: spacy"] and a["score"] == 1.0


def test_assess_is_deterministic_and_sorted():
    ev, bullet = ["Wrote a script."], "Led and owned the entire organization-wide script, architected end to end."
    runs = [sf.assess(bullet, ev) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]
    weights = [s.weight for s in runs[0]["signals"]]
    assert weights == sorted(weights, reverse=True) and runs[0]["score"] == sum(weights)


# ── the lists are small, lowercase and reviewable ────────────────────────────

def test_the_lists_are_plain_lowercase_words_and_phrases():
    word = re.compile(r"^[a-z0-9]+$")
    for c in sf._CLAIMS:
        for w in c.triggers | c.support:
            assert word.match(w), (c.concept, w)
        for p in (*c.phrases, *c.support_phrases):
            assert p == sf._norm_phrase(p), (c.concept, p)
        assert c.weight in (sf.STRONG, sf.SOFT), c.concept
    assert len({c.concept for c in sf._CLAIMS}) <= len(sf._CLAIMS)
    assert sum(len(c.triggers) + len(c.phrases) for c in sf._CLAIMS) < 140         # small enough to read in a sitting


def test_the_fit_is_recorded_under_the_fallbacks_own_model_name():
    doc = json.loads(policy.POLICY_PATH.read_text(encoding="utf-8"))
    entry = doc["fits"]["support_fallback"]
    assert entry["model"] == sf.MODEL == "fallback-support-v1" and entry["decision"] == sf.VERSION
    assert entry["script"] == "eval/fit_support_fallback.py" and entry["source_issue"] == 351
    assert entry["labels"][0]["sha256"] == policy.file_sha256(fit.PAIRS_PATH) and entry["recordings"] == []
    assert (sf.TAU_BLOCK, sf.TAU_REVIEW) == (1.0, 0.5) == tuple(
        doc["values"][k]["value"] for k in ("support_fallback.tau_block", "support_fallback.tau_review"))
    assert doc["version"] >= 2


# ── the fit ──────────────────────────────────────────────────────────────────

def test_a_lone_soft_signal_never_sets_the_block_cutoff():
    """With no supported pair scoring anything, the labels put `tau_block` anywhere from 0.5 up; the
    floor (one strong signal) is the rule that holds it at 1.0."""
    rec = fit.recommend(ROWS)
    assert (rec["tau_block"], rec["tau_review"]) == (1.0, 0.5) and rec["top_supported"]["score"] == 0
    rows = [{**r} for r in ROWS]
    for r in rows:
        if r["label"] == "supported" and r["id"] == "rw_rag_expand":
            r["score"] = 1.0                                   # a supported pair that scored a strong signal
    assert fit.recommend(rows)["tau_block"] == 1.5


def test_the_review_band_is_capped_at_a_tenth_of_the_supported_pairs():
    rows = [{**r} for r in ROWS]
    for i, r in enumerate(x for x in rows if x["label"] == "supported"):
        r["score"] = 0.5 if i < 5 else 0.0                     # 5 supported pairs in the band: over the cap of 3
    rec = fit.recommend(rows)
    assert rec["tau_review"] == rec["tau_block"] == 1.0           # no band fits the cap, so there is no review tier
    assert fit.band_stats(rows, rec["tau_review"], rec["tau_block"])["n"] == 0


def test_the_with_a_key_decision_follows_its_rule(isolated_engine, monkeypatch):
    """The floor is adopted only if it lowers the should-block pairs kept silently with no more false
    blocks. On Jev's recorded answers it does not (Jev keeps none silently), so Jev decides alone."""
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    monkeypatch.setattr(engine, "get_client", lambda: pytest.fail("replay reached for a client"))
    engine.reset_stats()
    recordings.import_recordings(json.loads(fit.RECORDINGS_PATH.read_text(encoding="utf-8")))
    jev_rows = fit.jev_fit.run_pairs(PAIRS)
    res = fit.with_key(ROWS, jev_rows, (sf.TAU_BLOCK, sf.TAU_REVIEW), (support.TAU_BLOCK, support.TAU_REVIEW))
    assert res["alone"]["block"] == 51 and res["alone"]["silent"] == 0 and res["alone"]["fp"] == 0
    assert res["floor"]["fp"] == 0 and res["floor"]["silent"] == 0 and res["floor"]["block"] == 53
    assert res["added_blocks"] == ["infl_wrote_designed", "infl_documented_owned"] and res["added_in_band"] == 2
    assert not fit.floor_adopted(res)
    assert fit.floor_adopted({"alone": {"silent": 2, "fp": 0}, "floor": {"silent": 1, "fp": 0}})
    assert not fit.floor_adopted({"alone": {"silent": 2, "fp": 0}, "floor": {"silent": 0, "fp": 1}})


def test_the_committed_report_is_current(isolated_engine, monkeypatch):
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    monkeypatch.setattr(engine, "get_client", lambda: pytest.fail("replay reached for a client"))
    engine.reset_stats()
    recordings.import_recordings(json.loads(fit.RECORDINGS_PATH.read_text(encoding="utf-8")))
    jev_rows = fit.jev_fit.run_pairs(PAIRS)
    assert fit.REPORT_PATH.read_text(encoding="utf-8") == fit.render_report(ROWS, jev_rows) + "\n"
    assert all(f"`{p['id']}`" in fit.REPORT_PATH.read_text(encoding="utf-8") for p in PAIRS)


# ── findings, gate and review format ─────────────────────────────────────────

KEY = "exp:engineer|acme"


def _page(bullet, cite_text):
    return ({"experiences": [{"title": "Engineer", "company": "Acme", "bullets": [bullet],
                              "cites": {bullet: [f"{KEY}#b0"]}}]}, {KEY: [cite_text]})


def test_a_heuristic_finding_blocks_reviews_or_passes_by_its_own_cutoffs():
    def f(score, label="adds_unsupported", status="heuristic"):
        return {"item": KEY, "bullet": "b", "status": status, "label": label, "p": None, "score": score,
                "signals": ["role: led"], "source": "fallback"}

    assert violations([f(sf.TAU_BLOCK)])[0].startswith(f'unsupported:{KEY}: "b" (fallback check: role: led)')
    assert violations([f(sf.TAU_BLOCK, "contradicts")][0:1])[0].startswith(f'contradicts:{KEY}')
    assert violations([f(sf.TAU_REVIEW)]) == [] and [r["score"] for r in reviews([f(sf.TAU_REVIEW)])] == [0.5]
    assert violations([f(0.4)]) == [] and reviews([f(0.4)]) == []
    assert violations([f(5.0, "supported")]) == [] and reviews([f(5.0, "supported")]) == []
    assert violations([f(5.0, status="unchecked")]) == []
    # Jev's thresholds do not apply to it, and its score is not a probability
    assert support.TAU_BLOCK == 0.85 and sf.TAU_BLOCK == 1.0


def test_a_checked_and_a_heuristic_finding_sort_together():
    jev = {"item": KEY, "bullet": "a", "status": "checked", "label": "adds_unsupported", "p": 0.5,
           "probabilities": {"supported": 0.5, "adds_unsupported": 0.5, "contradicts": 0.0}}
    fb = {"item": KEY, "bullet": "b", "status": "heuristic", "label": "adds_unsupported", "score": 0.5,
          "signals": ["role: platform"], "source": "fallback"}
    out = reviews([fb, jev])
    assert [r["bullet"] for r in out] == ["a", "b"] and "p" in out[0] and "score" in out[1]


def test_the_boundary_holds_for_the_new_module():
    """No model client under harness/ (tests/test_harness_boundary.py checks every module; this one by name)."""
    text = (ROOT / "harness" / "decisions" / "support_fallback.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(?:import|from)\s+(?:llm|openai|anthropic|langchain|langgraph)\b", text, re.M)
    assert "engine" not in re.findall(r"^\s*(?:import|from)\s+(\S+)", text, re.M)


# ── through the real executor, with no key ───────────────────────────────────

HL = "exp:software engineer|harbor labs"      # three source bullets, none of them claiming any seniority


def _revise(uid, job_id, replace_at, text, item=HL, **kw):
    """The item's source bullets, one reworded and citing its own source. "integrate" is a term of the
    posting the candidate's graph supports, so a clean reword improves coverage and is kept."""
    from harness.executor import _KG

    src = _KG(uid).source_bullets[item]
    bullets = [{"text": b, "cites": [f"{item}#b{i}"]} for i, b in enumerate(src)]
    bullets[replace_at] = {"text": text, "cites": [f"{item}#b{replace_at}"]}
    program = {"job_id": job_id, "nodes": [{"id": "n", "because": "job:the posting asks for it", "op": "revise", "item_key": item, "strategy": "keyword_weave",
                                            "keywords": ["integrate"], "bullets": bullets}]}
    return program, _run(uid, program, **kw)


@pytest.fixture()
def no_key(monkeypatch, auto):
    auto.use(Boom())                      # a transport that fails if anything is sent...
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("ART_JEV_MODE", "auto")
    monkeypatch.setattr(engine, "get_client", lambda: None)       # ...and no key, so nothing can be


ETL = "Wrote Python ETL jobs with pandas to integrate 50GB of daily event data into Snowflake."


def test_a_plan_that_inflates_a_role_and_adds_a_tool_is_reverted_with_no_key(env, no_key):
    uid, job_id, _ = env
    text = "Owned Python ETL jobs with pandas and Terraform to integrate 50GB of daily event data into Snowflake."
    _, out = _revise(uid, job_id, 1, text, dry_run=True)
    node = out["nodes"][0]
    assert node["status"] == "reverted" and "support" not in out
    assert node["reason"].startswith("hard_gate:")
    assert "consistency" in node["reason"] and "Terraform" in node["reason"]                  # #123: the new tool
    assert "faithfulness" in node["reason"] and "unsupported:" in node["reason"]               # #351: the role
    assert "(fallback check: role: owned)" in node["reason"]


def test_a_role_inflation_alone_is_reverted_by_the_fallback_not_by_the_numbers_gate(env, no_key):
    uid, job_id, _ = env
    _, out = _revise(uid, job_id, 1, "Led Python ETL jobs with pandas to integrate 50GB of daily event data "
                                     "into Snowflake.", dry_run=True)
    node = out["nodes"][0]
    assert node["status"] == "reverted" and "consistency" not in node["reason"]
    assert "faithfulness" in node["reason"] and "role: led" in node["reason"]
    assert out["metrics"]["final"]["gates"]["faithfulness"] == []                              # the page is unchanged


def test_a_contradiction_is_reverted_with_no_key(env, no_key):
    uid, job_id, _ = env
    _, out = _revise(uid, job_id, 2, "Added integration tests with pytest to integrate services and raised the "
                                     "flaky-test rate by half.", dry_run=True)
    node = out["nodes"][0]
    assert node["status"] == "reverted" and "contradicts:" in node["reason"]
    assert "reverses the direction of a change" in node["reason"]


def test_an_honest_reword_is_kept_with_no_review_and_nothing_added_to_the_result(env, no_key):
    uid, job_id, _ = env
    _, out = _revise(uid, job_id, 1, ETL, dry_run=True)
    node = out["nodes"][0]
    assert node["status"] == "accepted" and "review" not in node and "support" not in out


def test_a_soft_stretch_is_kept_and_surfaced_for_review(env, no_key):
    uid, job_id, _ = env
    _, out = _revise(uid, job_id, 1, ETL[:-1] + " on a shared platform.", dry_run=True)
    node = out["nodes"][0]
    assert node["status"] == "accepted"
    (r,) = node["review"]
    assert r["source"] == "fallback" and r["score"] == 0.5 and r["signals"] == ["role: platform"]
    assert out["support"] == {"checked": 0, "review": [r], "fallback_checked": 1}


def test_the_same_plan_with_jev_deciding_is_not_read_by_the_fallback(env, auto):
    """With a key Jev's answer is the finding; the fallback adds nothing beside it."""
    from test_jev import FakeTransport, choice_answer

    auto.use(FakeTransport(answer_for=lambda state, wire: choice_answer(
        "supported", {"supported": 0.97, "adds_unsupported": 0.02, "contradicts": 0.01})))
    uid, job_id, _ = env
    _, out = _revise(uid, job_id, 1, "Owned " + ETL[len("Wrote "):], dry_run=True)
    assert out["nodes"][0]["status"] == "accepted"
    assert out["support"] == {"checked": 1, "review": []}                                     # no `fallback_checked`


def test_a_run_with_no_key_is_deterministic(env, no_key):
    uid, job_id, _ = env
    text = "Owned " + ETL[len("Wrote "):]
    a = _revise(uid, job_id, 1, text, dry_run=True)[1]
    b = _revise(uid, job_id, 1, text, dry_run=True)[1]
    assert json.dumps(_strip(a), sort_keys=True) == json.dumps(_strip(b), sort_keys=True)
    assert a["nodes"][0]["status"] == "reverted"
