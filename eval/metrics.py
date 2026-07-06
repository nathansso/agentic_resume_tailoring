"""
Tailoring-quality metrics for the benchmark harness (issue #51).

Pure functions over a tailored_resume_content dict + JD text — no DB, no LLM —
so they are cheap to unit-test and reusable from pytest and the notebook.

Three metric families target the observed failure modes:
  - experience_allocation : does text volume per experience track JD relevance?
  - skills_metrics        : is the skills section selective and well organized?
  - redundancy_metrics    : are the same skill terms over-repeated across the resume?
plus ats_summary, which condenses the engine's baseline/tailored breakdowns.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from agents.ats_scorer import ATSScoringEngine
from agents.skill_scorer import MAX_SKILLS, MIN_SKILLS

# A skill term mentioned more than this many times across one resume reads as
# keyword stuffing (once in the skills section + a couple of bullets is fine).
OVER_REPEAT_THRESHOLD = 3


# ── helpers ────────────────────────────────────────────────────────────────────

def _ranks(values: List[float]) -> List[float]:
    """Average ranks (1-based) with ties sharing their mean rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        mean_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = mean_rank
        i = j + 1
    return ranks


def spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    """Spearman rank correlation; None when undefined (n<2 or zero variance)."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return None
    return round(cov / (vx * vy) ** 0.5, 3)


def _word_count(text: str) -> int:
    return len(text.split())


def _keyword_relevance(text: str, jd_keywords: set) -> float:
    """Fraction of the text's content tokens that appear in the JD keyword set."""
    tokens = ATSScoringEngine._extract_keywords(text)
    if not tokens:
        return 0.0
    return len(tokens & jd_keywords) / len(tokens)


# ── experience allocation ──────────────────────────────────────────────────────

def experience_allocation(tailored_content: Dict, jd_text: str) -> Dict:
    """
    Measures whether the amount of text an experience receives tracks its JD
    relevance. For each tailored experience: relevance = keyword overlap of its
    text with the JD; allocation = words across its bullets. Reports the
    Spearman correlation (1.0 = text budget perfectly follows relevance) and a
    per-experience table for inspection.
    """
    jd_keywords = ATSScoringEngine._extract_keywords(jd_text)
    rows = []
    for exp in tailored_content.get("experiences") or []:
        bullets = exp.get("bullets") or []
        text = " ".join([exp.get("title", ""), exp.get("description", "")] + bullets)
        rows.append({
            "title": exp.get("title", ""),
            "company": exp.get("company", ""),
            "relevance": round(_keyword_relevance(text, jd_keywords), 3),
            "bullets": len(bullets),
            "words": sum(_word_count(b) for b in bullets),
        })
    words = [r["words"] for r in rows]
    total_words = sum(words)
    for r in rows:
        r["word_share"] = round(r["words"] / total_words, 3) if total_words else 0.0
    return {
        "experiences": rows,
        "allocation_correlation": spearman(
            [r["relevance"] for r in rows], [float(r["words"]) for r in rows]
        ),
        "bullet_correlation": spearman(
            [r["relevance"] for r in rows], [float(r["bullets"]) for r in rows]
        ),
        "total_bullet_words": total_words,
    }


# ── skills organization ────────────────────────────────────────────────────────

def skills_metrics(
    tailored_content: Dict,
    matched_skills: Dict,
    total_profile_skills: int,
) -> Dict:
    """
    Selectivity and organization of the rendered skills section.

    - rendered_count / within_cap_bounds : is the section actually capped?
    - selection_ratio : rendered ÷ full profile (1.0 = "dumps everything")
    - matched_recall  : fraction of matcher-confirmed JD skills that survived
      into the rendered set (higher = the section keeps what matters)
    - category_count / categories : how the section is organized
    """
    ranked = tailored_content.get("skills_ranked") or []
    names = [s.get("name", "") for s in ranked]
    lower = {n.lower() for n in names}
    matched_names = [m.lower() for m in (matched_skills or {})]
    recall = (
        sum(1 for m in matched_names if m in lower) / len(matched_names)
        if matched_names else None
    )
    categories: List[str] = []
    for s in ranked:
        cat = s.get("category") or "Other"
        if cat not in categories:
            categories.append(cat)
    return {
        "rendered_count": len(names),
        "total_profile_skills": total_profile_skills,
        "selection_ratio": round(len(names) / total_profile_skills, 3)
        if total_profile_skills else None,
        "within_cap_bounds": (MIN_SKILLS <= len(names) <= MAX_SKILLS) if names else False,
        "matched_recall": round(recall, 3) if recall is not None else None,
        "category_count": len(categories),
        "categories": categories,
        "skills_rendered": names,
    }


# ── redundancy ─────────────────────────────────────────────────────────────────

def redundancy_metrics(tailored_content: Dict) -> Dict:
    """
    Over-repetition of skill terms across the whole rendered resume (bullets +
    skills section). A term named in the skills section that also appears in
    many bullets reads as keyword stuffing.

    - max_term_repetition / mean_term_repetition : occurrences per skill term
    - over_repeated : terms appearing more than OVER_REPEAT_THRESHOLD times
    - bullet_type_token_ratio : lexical variety across all bullets (lower =
      more repetitive writing overall)
    """
    ranked = tailored_content.get("skills_ranked") or []
    terms = [s.get("name", "").lower() for s in ranked if s.get("name")]
    if not terms:
        terms = [t.lower() for t in tailored_content.get("skills_emphasized") or []]

    full_text = ATSScoringEngine.flatten_tailored_text(tailored_content).lower()
    # Boundary-aware counting: "sql" must not match inside "mysql"/"sqlalchemy".
    counts = {
        t: len(re.findall(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", full_text))
        for t in terms if t
    }

    bullet_words: List[str] = []
    for key in ("experiences", "projects"):
        for item in tailored_content.get(key) or []:
            for b in item.get("bullets") or []:
                bullet_words.extend(w for w in b.lower().split() if len(w) > 2)
    ttr = round(len(set(bullet_words)) / len(bullet_words), 3) if bullet_words else None

    reps = sorted(counts.values(), reverse=True)
    over = {t: c for t, c in counts.items() if c > OVER_REPEAT_THRESHOLD}
    return {
        "term_counts": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "max_term_repetition": reps[0] if reps else 0,
        "mean_term_repetition": round(sum(reps) / len(reps), 2) if reps else 0.0,
        "over_repeated": dict(sorted(over.items(), key=lambda kv: -kv[1])),
        "over_repeated_count": len(over),
        "bullet_type_token_ratio": ttr,
    }


# ── ATS summary ────────────────────────────────────────────────────────────────

def ats_summary(baseline_breakdown: Dict, tailored_breakdown: Dict) -> Dict:
    """Condense the engine's breakdowns into composite + per-component deltas."""
    def comp(bd: Dict, key: str):
        v = (bd or {}).get(key)
        if isinstance(v, dict):
            return v.get("score")
        return v

    components = ["skill_coverage", "keyword_coverage", "section_presence", "role_level"]
    out: Dict = {
        "baseline_composite": (baseline_breakdown or {}).get("composite"),
        "tailored_composite": (tailored_breakdown or {}).get("composite"),
    }
    if out["baseline_composite"] is not None and out["tailored_composite"] is not None:
        out["delta"] = round(out["tailored_composite"] - out["baseline_composite"], 1)
    else:
        out["delta"] = None
    for key in components:
        b, t = comp(baseline_breakdown, key), comp(tailored_breakdown, key)
        out[key] = {
            "baseline": b,
            "tailored": t,
            "delta": round(t - b, 1) if b is not None and t is not None else None,
        }
    return out


# ── one-call rollup ────────────────────────────────────────────────────────────

def compute_task_metrics(
    tailored_content: Dict,
    jd_text: str,
    matched_skills: Dict,
    total_profile_skills: int,
    baseline_breakdown: Dict,
    tailored_breakdown: Dict,
) -> Dict:
    """All metric families for one benchmark task, as one JSON-serializable dict."""
    return {
        "ats": ats_summary(baseline_breakdown, tailored_breakdown),
        "experience_allocation": experience_allocation(tailored_content, jd_text),
        "skills": skills_metrics(tailored_content, matched_skills, total_profile_skills),
        "redundancy": redundancy_metrics(tailored_content),
    }
