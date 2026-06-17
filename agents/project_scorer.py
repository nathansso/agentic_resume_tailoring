"""
Project selection scoring (issue #46).

Composite score = RELEVANCE_WEIGHT * relevance + COMPLEXITY_WEIGHT * complexity.

Relevance (0-100) is the fraction of JD keywords found in the project's text
(name + description + blurbs) — the same keyword machinery as ATSScoringEngine.

Complexity (0-100) estimates how built-out/impressive a project is from the
average of saturating sub-signals (each min(value / cap, 1.0)):
  - linked_skills:  distinct skills evidencing the project in the knowledge graph
  - text_richness:  keyword-token count of description + blurbs
  - blurb_variety:  number of distinct pre-generated blurb styles
  - github_metrics: stars / language count / README length, omitted from the
    average when the project has no ingested GitHub metrics

The blend lets an impressive-but-adjacent project outrank a thin
exact-keyword-match project.

score_project() is a pure function over a project dict — no DB or LLM access.
Callers (agents/tailor.py) attach 'linked_skills' and 'metrics' to the dict
before scoring.
"""
from typing import Dict, List, Optional

from agents.ats_scorer import ATSScoringEngine

# Component weights — must sum to 1.0. Tune selection behavior here.
RELEVANCE_WEIGHT = 0.65
COMPLEXITY_WEIGHT = 0.35

# Dynamic top-k selection bounds (issue #47). k is chosen by a "drop-off + bounds"
# rule rather than a hardcoded count: always include MIN_PROJECTS, then keep adding
# the next project only while its score holds up against both the previous kept
# project and the top project, clamped at MAX_PROJECTS.
MIN_PROJECTS = 2
MAX_PROJECTS = 5
DROPOFF_RATIO = 0.60   # keep next project only if its score >= 60% of the previous kept score
TOP_RATIO = 0.50       # ...and >= 50% of the top score

# Saturation caps: a sub-signal reaches its maximum contribution at the cap.
LINKED_SKILLS_CAP = 8      # distinct knowledge-graph skills evidencing the project
TEXT_RICHNESS_CAP = 60     # keyword tokens across description + blurbs
BLURB_VARIETY_CAP = 4      # distinct blurb styles (concise/detailed/metrics/technical)
STARS_CAP = 25             # GitHub stargazers
LANGUAGES_CAP = 4          # GitHub language count
README_LENGTH_CAP = 2000   # README characters (ingestion truncates at 3000)


def _saturate(value: float, cap: float) -> float:
    """Map a raw signal onto [0, 1], saturating at the cap."""
    if value <= 0:
        return 0.0
    return min(value / cap, 1.0)


def _relevance(proj: Dict, jd_text: str) -> Dict:
    """Fraction of JD keywords found in the project's text, scaled to 0-100."""
    jd_keywords = ATSScoringEngine._extract_keywords(jd_text or "")
    if not jd_keywords:
        return {"score": 0.0, "matched": 0, "total": 0}

    parts = [proj.get("name") or "", proj.get("description") or ""]
    parts.extend(content or "" for content in (proj.get("blurbs") or {}).values())
    project_text = " ".join(parts).lower()

    hits = sum(1 for kw in jd_keywords if kw in project_text)
    return {
        "score": round(hits / len(jd_keywords) * 100, 1),
        "matched": hits,
        "total": len(jd_keywords),
    }


def _github_signal(metrics: Dict) -> Optional[float]:
    """Average of available GitHub sub-signals in [0, 1]; None when no metrics ingested."""
    if not metrics:
        return None
    signals = []
    if metrics.get("stars") is not None:
        signals.append(_saturate(metrics["stars"], STARS_CAP))
    if metrics.get("languages"):
        signals.append(_saturate(len(metrics["languages"]), LANGUAGES_CAP))
    if metrics.get("readme_length"):
        signals.append(_saturate(metrics["readme_length"], README_LENGTH_CAP))
    if not signals:
        return None
    return sum(signals) / len(signals)


def _complexity(proj: Dict) -> Dict:
    """Average of saturating depth signals, scaled to 0-100."""
    richness_text = " ".join(
        [proj.get("description") or ""]
        + [content or "" for content in (proj.get("blurbs") or {}).values()]
    )
    signals = {
        "linked_skills": _saturate(proj.get("linked_skills") or 0, LINKED_SKILLS_CAP),
        "text_richness": _saturate(
            len(ATSScoringEngine._extract_keywords(richness_text)), TEXT_RICHNESS_CAP
        ),
        "blurb_variety": _saturate(len(proj.get("blurbs") or {}), BLURB_VARIETY_CAP),
    }
    github = _github_signal(proj.get("metrics") or {})
    if github is not None:
        signals["github_metrics"] = github

    score = sum(signals.values()) / len(signals) * 100
    return {
        "score": round(score, 1),
        "signals": {name: round(value, 3) for name, value in signals.items()},
    }


def score_project(proj: Dict, jd_text: str) -> Dict:
    """
    Composite selection score for one project dict.

    Returns {"composite": float, "relevance": {...}, "complexity": {...}},
    all scores on a 0-100 scale.
    """
    relevance = _relevance(proj, jd_text)
    complexity = _complexity(proj)
    composite = (
        RELEVANCE_WEIGHT * relevance["score"] + COMPLEXITY_WEIGHT * complexity["score"]
    )
    return {
        "composite": round(composite, 1),
        "relevance": relevance,
        "complexity": complexity,
    }


def select_top_k(
    scored: List[Dict],
    *,
    score_key: str = "selection_score",
    min_k: int = MIN_PROJECTS,
    max_k: int = MAX_PROJECTS,
) -> List[Dict]:
    """
    Choose how many top projects to include using a drop-off + bounds rule (issue #47).

    `scored` must already be sorted descending by `score_key`. Returns the leading
    slice of `scored`:
      - always the first `min_k` (clamped to the list length),
      - then each subsequent project up to `max_k` only while its score is both
        >= DROPOFF_RATIO * the previous kept score and >= TOP_RATIO * the top score,
      - stopping at the first project that fails either test (the low-relevance tail
        is excluded).

    This balances including the most relevant projects against simply including too
    many: a gentle slope of strong scores fills up to `max_k`, while a sharp drop-off
    cuts the tail early.
    """
    if not scored:
        return []
    if len(scored) <= min_k:
        return list(scored)

    top_score = scored[0].get(score_key, 0) or 0
    selected = list(scored[:min_k])
    prev_score = selected[-1].get(score_key, 0) or 0

    for proj in scored[min_k:max_k]:
        score = proj.get(score_key, 0) or 0
        if score >= DROPOFF_RATIO * prev_score and score >= TOP_RATIO * top_score:
            selected.append(proj)
            prev_score = score
        else:
            break

    return selected
