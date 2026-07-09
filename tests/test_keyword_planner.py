"""
Tests for contextual keyword planning (issue #72).

Covers the three pure stages in agents/keyword_planner.py:
  - score_keywords: signal ranking (TF-IDF + skill boost + boilerplate penalty)
  - assign_keywords: contextual placement onto the right item, dropping misfits
  - evaluate_placement: scoring whether keywords landed where assigned
"""
from agents.keyword_planner import (
    ASSIGN_THRESHOLD,
    assign_keywords,
    evaluate_placement,
    keyword_fit,
    score_keywords,
    _jd_context,
    _tokenize,
)


JD = (
    "We need a data engineer to build kubernetes-deployed etl pipelines with "
    "airflow and spark. You will design terraform infrastructure and deploy "
    "scalable data pipelines. Kubernetes orchestration experience required. "
    "We are an equal opportunity employer offering competitive benefits."
)


# ── Stage 1: score_keywords ───────────────────────────────────────────────────

def test_score_keywords_ranks_distinctive_over_boilerplate():
    scored = score_keywords(
        ["kubernetes", "benefits", "airflow", "opportunity"], JD, corpus_texts=[JD]
    )
    ranked = [kw for kw, _ in scored]
    # Distinctive tech terms outrank HR boilerplate.
    assert ranked.index("kubernetes") < ranked.index("benefits")
    assert ranked.index("airflow") < ranked.index("opportunity")


def test_score_keywords_boosts_skill_terms():
    without = dict(score_keywords(["airflow"], JD, corpus_texts=[JD]))
    with_skill = dict(
        score_keywords(["airflow"], JD, corpus_texts=[JD], skill_terms=["Airflow"])
    )
    assert with_skill["airflow"] > without["airflow"]


def test_score_keywords_penalizes_boilerplate():
    scored = dict(score_keywords(["kubernetes", "benefits"], JD, corpus_texts=[JD]))
    assert scored["kubernetes"] > scored["benefits"]


def test_score_keywords_respects_top_k_and_is_deterministic():
    kws = ["kubernetes", "airflow", "spark", "terraform", "etl"]
    a = score_keywords(kws, JD, corpus_texts=[JD], top_k=3)
    b = score_keywords(list(reversed(kws)), JD, corpus_texts=[JD], top_k=3)
    assert len(a) == 3
    assert a == b  # order-independent, deterministic


def test_score_keywords_empty_input():
    assert score_keywords([], JD) == []


# ── Stage 2: assign_keywords ──────────────────────────────────────────────────

_ITEMS = [
    {"key": "exp:data", "source_text": (
        "Data Engineer at Acme. Built etl pipelines and airflow dags moving "
        "data into a warehouse. Wrote spark jobs."
    )},
    {"key": "exp:barista", "source_text": (
        "Barista at Cafe. Made espresso drinks and handled customer orders."
    )},
]


def test_keyword_lands_on_the_topically_right_item():
    scored = [("kubernetes", 1.0), ("terraform", 0.9)]
    assignments = assign_keywords(scored, _ITEMS, JD)
    # Infra keywords attach to the data engineering item, never the barista one.
    assert "exp:data" in assignments
    assert "exp:barista" not in assignments


def test_keyword_in_item_source_is_a_direct_hit():
    # 'airflow' already appears in the data item's source → strongest fit there.
    assignments = assign_keywords([("airflow", 1.0)], _ITEMS, JD)
    assert assignments.get("exp:data") == ["airflow"]


def test_unfittable_keyword_is_dropped_not_stuffed():
    # A keyword unrelated to any item and absent from the JD context fits nowhere.
    assignments = assign_keywords([("underwater", 1.0)], _ITEMS, JD)
    assert assignments == {}


def test_assignment_respects_per_item_cap():
    scored = [("kubernetes", 1), ("terraform", 1), ("spark", 1),
              ("airflow", 1), ("etl", 1), ("orchestration", 1)]
    assignments = assign_keywords(scored, _ITEMS, JD, max_per_item=2)
    assert all(len(v) <= 2 for v in assignments.values())


def test_keyword_fit_direct_beats_topical():
    ctx = {"pipelines", "data"}
    direct = keyword_fit("spark", {"spark", "data"}, ctx)
    topical = keyword_fit("spark", {"data"}, ctx)
    assert direct == 1.0
    assert 0.0 < topical < 1.0


def test_jd_context_gathers_cooccurring_tokens():
    ctx = _jd_context("kubernetes", JD)
    assert "orchestration" in ctx or "pipelines" in ctx
    assert "kubernetes" not in ctx  # the keyword itself is excluded


# ── Stage 3: evaluate_placement ───────────────────────────────────────────────

def test_evaluate_placement_rewards_correct_placement():
    assignments = {"exp:data": ["kubernetes", "terraform"]}
    rendered = {"exp:data": "Deployed kubernetes clusters with terraform infra."}
    result = evaluate_placement(assignments, rendered)
    assert result["precision"] == 1.0
    assert result["gaps"] == {}


def test_evaluate_placement_reports_gaps():
    assignments = {"exp:data": ["kubernetes", "terraform"]}
    rendered = {"exp:data": "Deployed kubernetes clusters."}
    result = evaluate_placement(assignments, rendered)
    assert result["gaps"] == {"exp:data": ["terraform"]}
    assert result["precision"] == 0.5


def test_evaluate_placement_flags_misplacement():
    # 'kubernetes' was assigned to exp:data but the model put it on exp:barista.
    assignments = {"exp:data": ["kubernetes"]}
    rendered = {
        "exp:data": "Built data pipelines.",
        "exp:barista": "Ran kubernetes on the espresso machine.",
    }
    result = evaluate_placement(assignments, rendered)
    assert result["misplaced"].get("exp:barista") == ["kubernetes"]
    assert result["gaps"].get("exp:data") == ["kubernetes"]


def test_evaluate_placement_whole_word_only():
    assignments = {"i": ["go"]}
    rendered = {"i": "Improved the algorithm."}  # 'go' inside 'algorithm' must not match
    result = evaluate_placement(assignments, rendered)
    assert result["precision"] == 0.0


def test_evaluate_placement_empty_is_perfect():
    assert evaluate_placement({}, {})["precision"] == 1.0
