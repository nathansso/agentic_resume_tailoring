"""
Skill selection scoring (issue #54).

Ranks a user's skills by relevance to a target job description and selects the
most relevant subset to render, so the Technical Skills section stops dumping
every skill alphabetically under a static category order.

Phase 1 (this module) is dependency-free and schema-free. It blends:
  - tfidf            : lexical importance of the skill's terms in *this* JD,
                       weighted by term rarity across a JD corpus (down-weights
                       generic terms, surfaces distinctive ones)
  - jd_weight        : how much the JD itself prioritizes the skill (JobSkill
                       weight + required flag), via matched_skills
  - match_confidence : direct > name > semantic > indirect match strength
  - proficiency      : the user's self-rated depth
  - evidence         : extraction confidence backing the skill

The semantic (embedding) component documented in #54 lands in Phase 2. The
composite is a weighted average over whichever components are *present* for a
run (normalized by their total weight), so adding 'semantic' later needs no
change here or in callers.

Pure functions over plain dicts — no DB or LLM access. agents/tailor.py loads
the skill rows + JD corpus and calls rank_and_select_skills().
"""
import math
from typing import Dict, List, Optional, Sequence

from agents.ats_scorer import (
    ATSScoringEngine,
    _MIN_WORD_LEN,
    _PURE_NUMBER,
    _SPLIT_PATTERN,
    _STOP_WORDS,
)

# ── Component weights (issue #54) ──────────────────────────────────────────────
# Composite = sum(weight * value) / sum(weight) over the components present for a
# run. The Phase-2 'semantic' key can be added without rebalancing the others.
WEIGHTS = {
    "semantic": 0.30,   # Phase 2 (embeddings) — not produced in Phase 1
    "tfidf": 0.20,
    "jd_weight": 0.20,
    "match_confidence": 0.10,
    "proficiency": 0.10,
    "evidence": 0.10,
}

# ── Dynamic cap bounds (mirrors project_scorer's drop-off + bounds rule, #47) ──
MIN_SKILLS = 8
MAX_SKILLS = 18
DROPOFF_RATIO = 0.55   # keep next skill only while >= 55% of previous kept score
TOP_RATIO = 0.20       # ...and >= 20% of the top score
CORE_FLOOR_K = 4       # always retain this many strongest skills (by proficiency)

PROFICIENCY_MAX = 5

# Match-type → confidence, aligned with SkillMatcherAgent's credit weights.
_MATCH_CONFIDENCE = {
    "direct": 1.0,
    "name_match": 0.9,
    "semantic": 0.75,
    "indirect": 0.5,
}


# ── IDF over a JD corpus ───────────────────────────────────────────────────────

def compute_idf(corpus_texts: Sequence[str]) -> Dict:
    """
    Document-frequency table over a corpus of JD texts, for inverse-document-
    frequency weighting. Returns {"N": doc_count, "df": {token: doc_freq}}.
    Reuses the ATS engine's tokenizer + stop-word list so tokens line up.
    """
    docs = [ATSScoringEngine._extract_keywords(t) for t in corpus_texts if t]
    df: Dict[str, int] = {}
    for doc in docs:
        for tok in doc:
            df[tok] = df.get(tok, 0) + 1
    return {"N": len(docs), "df": df}


def _idf_of(token: str, idf: Dict) -> float:
    """Smoothed IDF normalized to [0, 1]. Unknown/empty corpus → neutral 1.0."""
    n = idf.get("N", 0)
    if n == 0:
        return 1.0
    df = idf.get("df", {}).get(token, 0)
    raw = math.log((n + 1) / (df + 1)) + 1.0
    return raw / (math.log(n + 1) + 1.0)


def _jd_token_counts(jd_text: str) -> Dict[str, int]:
    """Token frequencies in the JD, filtered like ATSScoringEngine._extract_keywords."""
    counts: Dict[str, int] = {}
    for word in _SPLIT_PATTERN.split(jd_text.lower()):
        if (
            len(word) >= _MIN_WORD_LEN
            and word not in _STOP_WORDS
            and not _PURE_NUMBER.match(word)
        ):
            counts[word] = counts.get(word, 0) + 1
    return counts


# ── Component scorers (each returns a value in [0, 1]) ─────────────────────────

def _tfidf_component(skill_tokens: set, jd_counts: Dict[str, int], idf: Dict) -> float:
    """Best TF(saturated) x IDF over the skill's name tokens present in the JD."""
    best = 0.0
    for tok in skill_tokens:
        cnt = jd_counts.get(tok, 0)
        if cnt == 0:
            continue
        tf_sat = cnt / (cnt + 1.0)          # BM25-style saturation
        best = max(best, tf_sat * _idf_of(tok, idf))
    return best


def _lookup_match(matched_skills: Dict, name: str) -> Optional[Dict]:
    """Case-insensitive lookup of a skill in matched_skills."""
    if not matched_skills:
        return None
    if name in matched_skills:
        return matched_skills[name]
    lower = name.lower()
    for k, v in matched_skills.items():
        if k.lower() == lower:
            return v
    return None


# ── Orchestration ──────────────────────────────────────────────────────────────

def _present_components(skills: List[Dict], matched_skills: Dict) -> set:
    """Which weight keys are populated for this run (drives the normalization)."""
    present = {"tfidf"}
    if matched_skills:
        present.update({"jd_weight", "match_confidence"})
    if any(s.get("proficiency") is not None for s in skills):
        present.add("proficiency")
    if any((s.get("confidence") or 0) > 0 for s in skills):
        present.add("evidence")
    return present


def score_skills(
    skills: List[Dict],
    jd_text: str,
    matched_skills: Optional[Dict] = None,
    corpus_texts: Optional[Sequence[str]] = None,
) -> Optional[List[Dict]]:
    """
    Score every skill against the JD and return them sorted by composite score
    (desc), tie-broken by proficiency then name. Returns None when there is no
    JD signal to rank against, so the caller can fall back to the full list.

    Each input skill dict: {name, category, proficiency?, confidence?}.
    Each output skill dict adds {score, components}.
    """
    matched_skills = matched_skills or {}
    if not jd_text or not jd_text.strip():
        return None
    jd_counts = _jd_token_counts(jd_text)
    if not jd_counts:
        return None

    idf = compute_idf(corpus_texts or [])
    present = _present_components(skills, matched_skills)

    # Normalize JD requirement weight by the strongest matched weight.
    max_weight = 1.0
    if "jd_weight" in present:
        max_weight = max(
            (float(v.get("weight", 0)) for v in matched_skills.values()), default=1.0
        ) or 1.0

    scored: List[Dict] = []
    for s in skills:
        name = s.get("name", "")
        if not name:
            continue
        tokens = ATSScoringEngine._extract_keywords(name)
        comps: Dict[str, float] = {"tfidf": _tfidf_component(tokens, jd_counts, idf)}

        match = _lookup_match(matched_skills, name)
        if "jd_weight" in present:
            if match:
                base = float(match.get("weight", 0)) / max_weight
                if not match.get("required", True):
                    base *= 0.7
                comps["jd_weight"] = min(base, 1.0)
            else:
                comps["jd_weight"] = 0.0
        if "match_confidence" in present:
            comps["match_confidence"] = (
                _MATCH_CONFIDENCE.get(match.get("match_type", ""), 0.0) if match else 0.0
            )
        if "proficiency" in present:
            comps["proficiency"] = (s.get("proficiency") or 0) / PROFICIENCY_MAX
        if "evidence" in present:
            comps["evidence"] = min(float(s.get("confidence") or 0.0), 1.0)

        total_w = sum(WEIGHTS[k] for k in comps)
        composite = sum(WEIGHTS[k] * v for k, v in comps.items()) / total_w if total_w else 0.0

        scored.append({
            "name": name,
            "category": s.get("category") or "Other",
            "score": round(composite, 4),
            "proficiency": s.get("proficiency"),
            "components": {k: round(v, 4) for k, v in comps.items()},
        })

    scored.sort(
        key=lambda x: (x["score"], (x["proficiency"] or 0), _neg_name(x["name"])),
        reverse=True,
    )
    return scored


def _neg_name(name: str):
    """Sort helper so that with reverse=True, names break ties ascending (A→Z)."""
    return tuple(-ord(c) for c in name.lower())


def select_skills(scored: List[Dict]) -> List[Dict]:
    """
    Apply the dynamic cap (drop-off + MIN/MAX bounds) and the core-skill floor.
    `scored` must be sorted descending by 'score'. Output stays in score order;
    floored core skills with low JD relevance naturally sort to the end.
    """
    if not scored:
        return []
    if len(scored) <= MIN_SKILLS:
        return list(scored)

    top = scored[0]["score"] or 0.0
    selected = list(scored[:MIN_SKILLS])
    prev = selected[-1]["score"] or 0.0
    for s in scored[MIN_SKILLS:MAX_SKILLS]:
        score = s["score"] or 0.0
        if score >= DROPOFF_RATIO * prev and score >= TOP_RATIO * top:
            selected.append(s)
            prev = score
        else:
            break

    # Core-skill floor: guarantee the strongest skills (by proficiency) appear
    # even on an off-domain JD, so the section is never just low-relevance noise.
    chosen = {s["name"] for s in selected}
    core = sorted(
        scored, key=lambda x: ((x["proficiency"] or 0), x["score"]), reverse=True
    )[:CORE_FLOOR_K]
    for c in core:
        if c["name"] not in chosen:
            selected.append(c)
            chosen.add(c["name"])

    selected.sort(
        key=lambda x: (x["score"], (x["proficiency"] or 0), _neg_name(x["name"])),
        reverse=True,
    )
    return selected


def rank_and_select_skills(
    skills: List[Dict],
    jd_text: str,
    matched_skills: Optional[Dict] = None,
    corpus_texts: Optional[Sequence[str]] = None,
) -> Optional[List[Dict]]:
    """
    Full path: score → cap/floor. Returns the rendered skill list as
    [{name, category, score}, ...] in display order, or None when there is no
    JD signal (caller falls back to the untailored full list).
    """
    scored = score_skills(skills, jd_text, matched_skills, corpus_texts)
    if scored is None:
        return None
    selected = select_skills(scored)
    return [{"name": s["name"], "category": s["category"], "score": s["score"]} for s in selected]
