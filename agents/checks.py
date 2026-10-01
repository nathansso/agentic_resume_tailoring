"""Model-free tailoring checks and plan helpers (issue #190).

Moved verbatim out of `agents/tailor.py` so the harness executor (#197), the
citation gate (#198) and the line budget (#200) can use them without loading
the LLM stack. `agents/tailor.py` re-imports every name here: the constants are
re-exported from it, and each function stays reachable as the same-named
`ResumeTailorAgent._<name>` staticmethod alias, so existing callers and tests
are unaffected.

**This module must never import a generative model client** (`llm`,
`langchain_*`, `langgraph`, `openai`, `anthropic`) — not even lazily.
`tests/test_harness_boundary.py` walks the import graph to enforce it.
"""
import re
from decimal import Decimal
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from agents.ats_scorer import ATSScoringEngine
from agents.skill_scorer import _env_float, _env_int

# Per-experience bullet budgets: the most JD-relevant experience gets up to
# MAX_EXP_BULLETS bullets, the least relevant as few as MIN_EXP_BULLETS, so
# text volume tracks relevance instead of every experience getting equal space.
# The floor is 2 (not 1): a single-bullet experience reads as an afterthought,
# and issue #72 asks that we revise/keep experiences rather than starve them.
MAX_EXP_BULLETS = _env_int("TAILOR_MAX_EXP_BULLETS", 4)
MIN_EXP_BULLETS = _env_int("TAILOR_MIN_EXP_BULLETS", 2)

# A skill term used more than this many times across the tailored output reads
# as keyword stuffing; the evaluator flags offenders into retry feedback.
MAX_TERM_MENTIONS = _env_int("TAILOR_MAX_TERM_MENTIONS", 3)

# Revision faithfulness (issue #72): mean token overlap between a revised bullet
# and its closest source bullet. Below this, the "revision" drifted far enough
# from the source to read as a rewrite; the evaluator nudges the retry to stay
# closer to the original wording. Lenient by design — keyword insertion and
# tightening legitimately lower overlap.
FAITHFULNESS_MIN = _env_float("TAILOR_FAITHFULNESS_MIN", 0.2)

# Section ordering (issue #22). The name/contact header is rendered by the
# formatter above all sections and is never part of section_order.
#
# Nothing is pinned any more (issue #118): education used to be forced to the
# top, which made it the one section a user could not move and the one section
# JD relevance could not place. A degree in the field the posting names should
# be able to lead; a decade-old degree behind three relevant jobs should not.
# It is now ranked like every other section and, like every other section, an
# explicit user override outranks the ranker.
PINNED_SECTIONS: List[str] = []
REORDERABLE_SECTIONS = [
    "education", "experience", "projects", "skills", "achievements",
]
# Sections that exist only when the user has rows for them. Keyed by the
# content key that holds those rows, which is also the section key.
_CONTENT_GATED_SECTIONS = {"education", "achievements"}


def score_and_budget_experiences(exp_dicts: List[Dict], jd_text: str) -> List[Dict]:
    """
    Order experiences by JD relevance (keyword overlap, like project
    selection) and attach a bullet_budget: the most relevant experience may
    keep up to MAX_EXP_BULLETS bullets, the least relevant as few as
    MIN_EXP_BULLETS. No JD signal → order and budgets left untouched.
    """
    jd_keywords = ATSScoringEngine._extract_keywords(jd_text)
    if not exp_dicts or not jd_keywords:
        return exp_dicts

    scored = []
    for e in exp_dicts:
        text = " ".join(
            [e.get("title") or "", e.get("description") or ""]
            + list(e.get("bullets") or [])
        )
        tokens = ATSScoringEngine._extract_keywords(text)
        rel = len(tokens & jd_keywords) / len(tokens) if tokens else 0.0
        scored.append((rel, e))

    max_rel = max(rel for rel, _ in scored)
    out = []
    # Stable sort: relevance ties keep the original (chronological) order.
    for rel, e in sorted(scored, key=lambda t: t[0], reverse=True):
        norm = rel / max_rel if max_rel > 0 else 1.0
        budget = MIN_EXP_BULLETS + round(norm * (MAX_EXP_BULLETS - MIN_EXP_BULLETS))
        out.append({**e, "relevance_score": round(rel, 3), "bullet_budget": budget})
    return out


def enforce_bullet_budgets(generated: List[Dict], budgeted: List[Dict]) -> List[Dict]:
    """
    Deterministic guarantee behind the prompt's bullet_budget rule (issue #72):

    - Reorder the LLM's experiences to the pre-ranked relevance order and
      truncate any that exceed their budget.
    - Re-attach dates and canonical title/company from the source rows rather
      than trusting the LLM round trip — the model must not author or malform
      dates (same rationale as project links, issue #75).
    - Restore experiences the model silently dropped: it may shorten an
      experience, never delete one. The number restored is bounded by how many
      the model actually omitted, so a *renamed* experience (count preserved)
      is treated as a replacement, not a deletion, and is not duplicated.

    Experiences the LLM renamed keep their bullets and sort after the
    recognized ones, in original relative order.
    """
    def key(e: Dict) -> tuple:
        return ((e.get("title") or "").strip().lower(),
                (e.get("company") or "").strip().lower())

    rank = {key(e): i for i, e in enumerate(budgeted)}
    source = {key(e): e for e in budgeted}
    fallback = len(budgeted)

    def _trim(bullets: List, budget) -> List:
        bullets = bullets or []
        return bullets[:budget] if budget and len(bullets) > budget else bullets

    out: List[Dict] = []
    seen: set = set()
    for e in sorted(generated or [], key=lambda e: rank.get(key(e), fallback)):
        k = key(e)
        src = source.get(k)
        if src is not None:
            seen.add(k)
            e = {
                **e,
                "title": src.get("title", e.get("title")),
                "company": src.get("company", e.get("company")),
                "start_date": src.get("start_date"),
                "end_date": src.get("end_date"),
                "bullets": _trim(e.get("bullets"), src.get("bullet_budget")),
            }
        out.append(e)

    # Restore only as many missing experiences as the model actually dropped
    # (len(source) - len(generated)); renames preserve the count and restore 0.
    n_missing = max(0, len(budgeted) - len(generated or []))
    if n_missing:
        unseen = [src for k, src in source.items() if k not in seen]
        for src in unseen[:n_missing]:
            out.append({
                "title": src.get("title"),
                "company": src.get("company"),
                "start_date": src.get("start_date"),
                "end_date": src.get("end_date"),
                "bullets": _trim(src.get("bullets"), src.get("bullet_budget")),
            })

    out.sort(key=lambda e: rank.get(key(e), fallback))
    return out


def exp_key(e: Dict) -> str:
    return f"exp:{(e.get('title') or '').strip().lower()}|{(e.get('company') or '').strip().lower()}"


def proj_key(p: Dict) -> str:
    return f"proj:{(p.get('name') or '').strip().lower()}"


def label_for_key(key: str, state: "TailorState") -> str:
    """Human-readable label (title/name) for an item key, for retry feedback."""
    if key.startswith("exp:"):
        for e in state.get("experiences") or []:
            if exp_key(e) == key:
                return e.get("title") or key
    elif key.startswith("proj:"):
        for p in state.get("projects") or []:
            if proj_key(p) == key:
                return p.get("name") or key
    return key


def rendered_by_key(tailored_content: Dict) -> Dict[str, str]:
    """Map each generated item to its rendered bullet text, keyed like the
    assignments, so placement can be scored per item (issue #72)."""
    out: Dict[str, str] = {}
    for e in tailored_content.get("experiences") or []:
        out[exp_key(e)] = " ".join(e.get("bullets") or [])
    for p in tailored_content.get("projects") or []:
        out[proj_key(p)] = " ".join(p.get("bullets") or [])
    return out


def faithfulness_drift(tailored_content: Dict, source_experiences: List[Dict]) -> List[str]:
    """
    Item labels whose revised bullets drifted far from their source bullets
    (issue #72) — a signal that the model rewrote rather than revised. Uses
    mean best-Jaccard of each source bullet against the generated bullets;
    below FAITHFULNESS_MIN the item is flagged. Items with no source bullets
    (nothing to preserve) are skipped.
    """
    def toks(s: str) -> set:
        return ATSScoringEngine._extract_keywords(s or "")

    src_by_key = {
        f"{(e.get('title') or '').strip().lower()}|{(e.get('company') or '').strip().lower()}": e
        for e in source_experiences
    }
    drifted: List[str] = []
    for gen in tailored_content.get("experiences") or []:
        key = f"{(gen.get('title') or '').strip().lower()}|{(gen.get('company') or '').strip().lower()}"
        src = src_by_key.get(key)
        if not src:
            continue
        src_bullets = src.get("bullets") or []
        gen_toks = [toks(b) for b in (gen.get("bullets") or [])]
        if not src_bullets or not gen_toks:
            continue
        overlaps = []
        for sb in src_bullets:
            st = toks(sb)
            if not st:
                continue
            best = max(
                (len(st & gt) / len(st | gt) if (st | gt) else 0.0) for gt in gen_toks
            )
            overlaps.append(best)
        if overlaps and sum(overlaps) / len(overlaps) < FAITHFULNESS_MIN:
            drifted.append(gen.get("title") or key)
    return drifted


def relevance_density(text: str, jd_keywords: set) -> float:
    """Fraction of the text's content tokens that appear in the JD keyword set.

    Not monotone in text, unlike the ATS composite: padding a bullet with words
    the posting never uses lowers it, and cutting an irrelevant item raises it.
    That is why it is a tailoring *target* (docs/harness.md § 5). Promoted from
    `eval/metrics._keyword_relevance` for the executor (#197).
    """
    tokens = ATSScoringEngine._extract_keywords(text)
    if not tokens:
        return 0.0
    return len(tokens & jd_keywords) / len(tokens)


def over_repeated_terms(tailored_content: Dict) -> Dict[str, int]:
    """
    Skill terms mentioned more than MAX_TERM_MENTIONS times across the whole
    tailored output (bullets + skills), boundary-aware so "sql" does not
    match inside "mysql". Fed back to the generator as anti-stuffing gaps.
    """
    terms = {
        str(t).lower()
        for t in tailored_content.get("skills_emphasized") or []
        if t
    }
    text = ATSScoringEngine.flatten_tailored_text(tailored_content).lower()
    over: Dict[str, int] = {}
    for term in terms:
        count = len(re.findall(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text))
        if count > MAX_TERM_MENTIONS:
            over[term] = count
    return dict(sorted(over.items(), key=lambda kv: -kv[1]))


def expected_sections(tailored_content: Dict) -> List[str]:
    """Which sections a valid order must name, for this content.

    Single source of truth for section membership, read both by
    `_ranked_section_order` and by the carry-forward validity check in
    `tailor()` (issue #115) — a prior order must not survive into a run
    whose section set has changed under it.

    Achievements and education are optional: only place them when the user
    actually has some, so a user without either keeps a clean section order
    rather than one naming a section the formatter will render empty.
    """
    seed = [
        k for k in REORDERABLE_SECTIONS
        if k not in _CONTENT_GATED_SECTIONS or tailored_content.get(k)
    ]
    return PINNED_SECTIONS + seed


def annotate_with_action(item: Dict, action: Optional[Dict]) -> Dict:
    """Copy plan fields onto the generator-facing item dict. The generator
    prompt reads plan_op / plan_strategy / plan_keywords per item."""
    if not action:
        return item
    out = {**item, "plan_op": action.get("op"),
           "plan_rationale": action.get("rationale") or ""}
    if action.get("op") == "revise":
        out["plan_strategy"] = action.get("strategy")
        out["plan_keywords"] = action.get("keywords") or []
    return out


def apply_plan_to_inputs(plan: Dict,
    exp_dicts: List[Dict],
    proj_dicts: List[Dict],
    proj_pool: List[Dict],
    keyword_assignments: Dict,
    prior_content: Dict,
) -> tuple:
    """
    Apply the validated plan structurally to the generator inputs
    (issue #91): deletes remove the item, replaces swap the pool candidate
    in at the same position, keep/revise annotate the item with its plan
    fields. Keyword assignments for removed items are dropped so the
    placement evaluator doesn't demand keywords on items that no longer
    exist. Returns (exp_dicts, proj_dicts, keyword_assignments).
    """
    actions_by_key = {
        a.get("item_key"): a for a in (plan.get("actions") or [])
    }
    pool_by_key = {proj_key(p): p for p in proj_pool}
    prior_bullets_by_key = {
        proj_key(p): p.get("bullets")
        for p in (prior_content or {}).get("projects") or []
    }
    # Experiences need the same lookup: without it a kept experience fell
    # back to its raw knowledge-graph bullets in _enforce_plan, so `keep`
    # discarded the tailoring instead of preserving it (issue #115).
    prior_exp_bullets_by_key = {
        exp_key(e): e.get("bullets")
        for e in (prior_content or {}).get("experiences") or []
    }

    removed: set = set()

    new_exps: List[Dict] = []
    for e in exp_dicts:
        key = exp_key(e)
        action = actions_by_key.get(key)
        if action and action.get("op") == "delete":
            removed.add(key)
            continue
        annotated = annotate_with_action(e, action)
        # A kept experience on a re-tailor carries its prior tailored
        # bullets so enforcement can restore them verbatim (issue #115),
        # mirroring the project branch below.
        if action and action.get("op") == "keep" and prior_exp_bullets_by_key.get(key):
            annotated["prior_bullets"] = prior_exp_bullets_by_key[key]
        new_exps.append(annotated)

    new_projs: List[Dict] = []
    for p in proj_dicts:
        key = proj_key(p)
        action = actions_by_key.get(key)
        op = action.get("op") if action else None
        if op == "delete":
            removed.add(key)
            continue
        if op == "replace":
            repl = pool_by_key.get(action.get("replacement_key"))
            if repl is not None:
                removed.add(key)
                new_projs.append({
                    **repl,
                    "plan_op": "revise",
                    "plan_strategy": "reframe",
                    "plan_keywords": [],
                    "plan_rationale": action.get("rationale") or "",
                })
                continue
            # Validation guarantees the key exists; if not, fall through
            # and keep the original rather than losing a project.
            action = None
        annotated = annotate_with_action(p, action)
        # A kept project on a re-tailor carries its prior tailored bullets
        # so enforcement can restore them verbatim (issue #91).
        if action and action.get("op") == "keep" and prior_bullets_by_key.get(key):
            annotated["prior_bullets"] = prior_bullets_by_key[key]
        new_projs.append(annotated)

    assignments = {
        k: v for k, v in (keyword_assignments or {}).items() if k not in removed
    }
    return new_exps, new_projs, assignments


def enforce_plan(tailored: Dict, state: "TailorState") -> Dict:
    """
    Deterministic guarantee behind the plan (issues #91/#51): the LLM must
    not undo a planner decision.

    - Experiences/projects the plan deleted or replaced are dropped from
      the output even if the model regenerated them.
    - plan_op=keep items restore their prior *tailored* bullets verbatim
      when a re-tailor supplied them (trimmed to budget), for experiences
      and projects alike — `keep` means keep the tailoring (issue #115).

    The two branches differ only in their first-run fallback, when there is
    no prior tailored content to carry: an experience falls back to its
    knowledge-graph source bullets, which is what `keep` means before
    anything has been tailored, while a project keeps what the generator
    produced. That difference is deliberate; the carry-forward rule above
    is not allowed to diverge again.
    """
    plan = state.get("plan") or {}
    actions = plan.get("actions") or []
    if not actions:
        return tailored

    gone = {
        a.get("item_key") for a in actions
        if a.get("op") in ("delete", "replace")
    }
    # Survivor keys: items that remain in the generator inputs. A replace
    # removes the original key but its replacement is a survivor.
    exp_by_key = {exp_key(e): e for e in state.get("experiences") or []}
    proj_by_key = {proj_key(p): p for p in state.get("projects") or []}

    if tailored.get("experiences"):
        out = []
        for gen in tailored["experiences"]:
            key = exp_key(gen)
            if key in gone and key not in exp_by_key:
                continue
            src = exp_by_key.get(key)
            if src is not None and src.get("plan_op") == "keep":
                budget = src.get("bullet_budget")
                bullets = list(src.get("prior_bullets") or src.get("bullets") or [])
                gen = {**gen, "bullets": bullets[:budget] if budget else bullets}
            out.append(gen)
        tailored["experiences"] = out

    if tailored.get("projects"):
        out = []
        for gen in tailored["projects"]:
            key = proj_key(gen)
            if key in gone and key not in proj_by_key:
                continue
            src = proj_by_key.get(key)
            if src is not None and src.get("plan_op") == "keep" and src.get("prior_bullets"):
                budget = src.get("bullet_budget")
                bullets = list(src["prior_bullets"])
                gen = {**gen, "bullets": bullets[:budget] if budget else bullets}
            out.append(gen)
        tailored["projects"] = out

    return tailored


# ── Line budget (issue #200) ──────────────────────────────────────────────────
#
# Pure budget math over rendered line counts; nothing here compiles. The counts
# come from `harness/render_cache.py`, which measures each bullet once with the
# real preamble and engine and caches it by (text hash, template hash).
#
# Units: one line = one `\small` bullet line (12pt baselineskip). Everything
# else on the page is expressed in those units as a calibrated overhead.
#
# Calibration (2026-09-25, tectonic 0.16.9, Jake's preamble in
# `agents/formatter.py`):
#
# - **Fit.** 120 random resumes built by `_build_tex` (0-2 education rows, 0-4
#   experiences with 0-5 bullets, 0-4 projects with 0-4 bullets, synthetic
#   bullets of 1-3 lines, 0-6 skill categories) were compiled on a page 80in
#   taller, and TeX logged `\pagetotal - \pageshrink` at the end: the height
#   the page builder needs once list and section glue has shrunk. Least
#   squares on that height, with each bullet line fixed at exactly 12pt, gave
#   the constants below (divided by 12). Residuals: RMSE 0.28 lines, worst 0.70.
#   A per-bullet overhead fitted to -0.01 lines (the 2pt item gap shrinks
#   away), so a bullet costs exactly its lines.
# - **Budget.** The text area is 10in = 722.7pt = 60.2 lines, and "needed
#   height <= 722.7pt" matched the real page count on 119 of 120 compiles.
#   Hence a default budget of 60 lines, not 53.
# - **Held out.** 180 resumes assembled from the real bullets in
#   `eval/profiles/*.md` (lines measured by `harness/render_cache.py`), each
#   compiled for its true page count: the estimate is biased +0.3 lines
#   (conservative), RMSE 0.6, worst 1.3. `over_by > 0` predicted one page vs
#   overflow correctly on 178 of 180; both misses were false alarms that fit
#   with under a line to spare.
#
# Achievements were calibrated once the block compiled (#217): the same
# `\pagetotal - \pageshrink` difference with and without the section, for 1-8
# one-line achievements, is 2.081 + 1.000 per item with zero residual. That is
# the section cost plus `item_list` (0.21), hence 1.87.
MAX_BULLET_LINES = 2
PAGE_LINE_BUDGET = 60

LINE_COSTS: Dict[str, float] = {
    "header": 3.30,              # page top + name + contact line
    "section:education": 1.65,   # \section rule + outer list, per present section
    "section:experience": 2.16,
    "section:projects": 1.93,
    "section:skills": 3.18,
    "section:achievements": 1.87,  # \section rule + the one-item outer list (#217)
    "entry:education": 2.58,     # two-line \resumeSubheading
    "entry:experience": 2.16,    # two-line \resumeSubheading
    "entry:project": 1.20,       # one-line \resumeProjectHeading
    "item_list": 0.21,           # an entry's \resumeItemListStart/End
    "skill_line": 1.13,          # one skills category (assumed not to wrap)
}


def _ach_key(a: Dict) -> str:
    # Same formula as `harness.tools.ach_key`; kept here so this module never
    # imports the harness.
    return f"ach:{(a.get('title') or '').strip().lower()}"


def bullet_id(item_key: str, index: int) -> str:
    """Stable id of one bullet: its item's planner key plus its index in the
    item's `bullets` list (blank bullets keep their index but never render)."""
    return f"{item_key}#b{index}"


def _rendered_bullets(item: Dict) -> List[Tuple[int, str]]:
    return [(i, b) for i, b in enumerate(item.get("bullets") or []) if b and b.strip()]


def _achievement_items(content: Dict) -> List[Dict]:
    return [a for a in (content.get("achievements") or []) if (a.get("title") or "").strip()]


def achievement_text(a: Dict) -> str:
    """An achievement as bullet text whose inline conversion is the LaTeX the
    formatter emits for it: `\\textbf{title} (issuer, date): description`."""
    meta = ", ".join(x for x in ((a.get("issuer") or "").strip(),
                                 (a.get("date") or "").strip()) if x)
    text = f"**{a['title'].strip()}**" + (f" ({meta})" if meta else "")
    desc = (a.get("description") or "").strip()
    return text + (f": {desc}" if desc else "")


def bullet_texts(content: Dict) -> Dict[str, str]:
    """`{bullet id: text}` for every block the page budget counts, in render
    order: experience bullets, project bullets, then achievement entries (keyed
    by their `ach:` key). Feed the values to `render_cache.bullet_lines`."""
    out: Dict[str, str] = {}
    for e in content.get("experiences") or []:
        for i, b in _rendered_bullets(e):
            out[bullet_id(exp_key(e), i)] = b
    for p in content.get("projects") or []:
        for i, b in _rendered_bullets(p):
            out[bullet_id(proj_key(p), i)] = b
    for a in _achievement_items(content):
        out[_ach_key(a)] = achievement_text(a)
    return out


def bullet_line_violations(lines_by_bullet: Dict[str, int],
                           max_lines: int = MAX_BULLET_LINES) -> List[Dict]:
    """The hard gate: every bullet rendering to more than `max_lines` lines."""
    return [{"bullet": bid, "lines": n, "max": max_lines}
            for bid, n in lines_by_bullet.items() if n > max_lines]


def _skill_line_count(content: Dict) -> int:
    cats = []
    for s in content.get("skills_ranked") or []:
        cat = s.get("category") or "Other"
        if s.get("name") and cat not in cats:
            cats.append(cat)
    return len(cats)


def page_line_budget(content: Dict, lines_by_bullet: Dict[str, int],
                     budget: float = PAGE_LINE_BUDGET, *,
                     skill_lines: Optional[int] = None,
                     education_entries: Optional[int] = None,
                     header: bool = True) -> Dict[str, Any]:
    """Estimate the page's height in bullet lines and, when it is over budget,
    which cuts would bring it back.

    `lines_by_bullet` must hold every id `bullet_texts(content)` returns.
    Tailored content carries neither education (the formatter reads it from
    the store) nor a finished skills block, so both can be passed in:
    `education_entries` defaults to `len(content["education"])` and
    `skill_lines` to the number of distinct categories in
    `content["skills_ranked"]`.

    Returns `{lines_used, budget, over_by, cut_hints}`. `over_by > 0` predicts
    a second page. `cut_hints` follow `agents.formatter._trim_one_bullet`'s
    ladder — project bullets down to 2, then experience bullets down to 2, then
    whole projects (keeping one), then below the floor — and stop once the
    lines they free cover `over_by`. Each hint names what to cut and the lines
    it frees; nothing is cut here.
    """
    missing = [bid for bid in bullet_texts(content) if bid not in lines_by_bullet]
    if missing:
        raise ValueError(f"no rendered line count for: {', '.join(missing)}")
    c = LINE_COSTS
    exps = content.get("experiences") or []
    projs = content.get("projects") or []
    achs = _achievement_items(content)
    n_edu = (len(content.get("education") or []) if education_entries is None
             else education_entries)
    n_skill = _skill_line_count(content) if skill_lines is None else skill_lines

    def entry_cost(item: Dict, key: str, heading: float) -> float:
        bullets = _rendered_bullets(item)
        return heading + (c["item_list"] if bullets else 0.0) + sum(
            lines_by_bullet[bullet_id(key, i)] for i, _ in bullets)

    used = c["header"] if header else 0.0
    if n_edu:
        used += c["section:education"] + n_edu * c["entry:education"]
    if exps:
        used += c["section:experience"] + sum(
            entry_cost(e, exp_key(e), c["entry:experience"]) for e in exps)
    if projs:
        used += c["section:projects"] + sum(
            entry_cost(p, proj_key(p), c["entry:project"]) for p in projs)
    if n_skill:
        used += c["section:skills"] + n_skill * c["skill_line"]
    if achs:
        used += c["section:achievements"] + c["item_list"] + sum(
            lines_by_bullet[_ach_key(a)] for a in achs)

    used = round(used, 2)
    over_by = round(used - budget, 2)
    return {"lines_used": used, "budget": budget, "over_by": over_by,
            "cut_hints": _cut_hints(exps, projs, lines_by_bullet, over_by)}


# Mirrors agents.formatter._PROJECT_BULLET_FLOOR / _EXP_BULLET_FLOOR (issue #72).
_PROJECT_FLOOR = 2
_EXP_FLOOR = 2


def _cut_hints(exps: List[Dict], projs: List[Dict],
               lines_by_bullet: Dict[str, int], over_by: float) -> List[Dict]:
    """Walk `_trim_one_bullet`'s ladder on bullet ids until `over_by` is freed."""
    if over_by <= 0:
        return []
    c = LINE_COSTS
    # Working copy: [key, [bullet ids]] per item, in content order.
    work_e = [[exp_key(e), [bullet_id(exp_key(e), i) for i, _ in _rendered_bullets(e)]]
              for e in exps]
    work_p = [[proj_key(p), [bullet_id(proj_key(p), i) for i, _ in _rendered_bullets(p)]]
              for p in projs]

    def shave(items, floor):
        for key, ids in reversed(items):
            if len(ids) > floor:
                bid = ids.pop()
                return {"action": "cut_bullet", "item": key, "bullet": bid,
                        "frees": float(lines_by_bullet[bid])}
        return None

    def step():
        hint = shave(work_p, _PROJECT_FLOOR) or shave(work_e, _EXP_FLOOR)
        if hint:
            return hint
        if len(work_p) > 1:
            key, ids = work_p.pop()
            frees = c["entry:project"] + sum(lines_by_bullet[b] for b in ids) + (
                c["item_list"] if ids else 0.0)
            return {"action": "drop_item", "item": key, "bullets": ids,
                    "frees": round(frees, 2)}
        return shave(work_e, 1) or shave(work_p, 1)

    hints, freed = [], 0.0
    while freed < over_by:
        hint = step()
        if hint is None:
            break
        hints.append(hint)
        freed += hint["frees"]
    return hints


# ── numeric and entity consistency (issue #123) ──────────────────────────────
#
# A tailored bullet may not contain a number, date, duration, money or scale
# figure, or a proper noun / technology name, that its evidence does not. Regex
# and word lists only: no model, no calibration. (The semantic half of
# faithfulness is #193's Jev check; Jev is documented weak on numbers, so the
# numerals are owned here.)
#
# Both sides go through the same extraction, so an odd token (3D, GPT-4)
# cancels itself. Strict by design: a gate that is too strict is at least
# visible. Known strictness, recorded rather than hidden:
#   - a derivation ("200 to 800 users" -> "4x growth") is flagged;
#   - `one` and `zero` written as words are never claims in a bullet (too many
#     idioms: "one of", "zero-downtime"), and a number word hyphenated into a
#     compound ("three-tier") is not one either. The evidence side reads them
#     all, so "three-tier" supports "3-tier";
#   - "a dozen", "half" and fractions in words are not read.

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split())}
_SCALES = {"hundred": 100, "thousand": 10**3, "million": 10**6, "billion": 10**9,
           "trillion": 10**12}
_NUMWORD = "|".join(sorted([*_UNITS, *_TENS, *_SCALES], key=len, reverse=True))
_WORD_RUN = re.compile(
    rf"(?<![A-Za-z])(?:{_NUMWORD})(?:(?:[\s-]+(?:and\s+)?|\s+and\s+)(?:{_NUMWORD}))*(?![A-Za-z])",
    re.I)
_A_SCALE = re.compile(r"\b[Aa](?=\s+(?:hundred|thousand|million|billion|trillion)\b)")
_COMPOUND_UNIT = re.compile(r"-(?:second|minute|hour|day|week|month|year|percent)", re.I)


def _parse_word_run(run: str) -> Optional[int]:
    """`twenty-five` -> 25, `two million` -> 2000000; None when the words do
    not compose into one number (`three four`)."""
    total = cur = 0
    prev = None                     # unit | teen | tens | hundred | scale
    for w in re.split(r"[\s-]+", run.lower()):
        if w == "and":
            continue
        if w in _UNITS:
            v = _UNITS[w]
            kind = "teen" if v >= 10 else "unit"
            if prev in ("unit", "teen") or (prev == "tens" and kind != "unit") \
                    or (v == 0 and prev is not None):
                return None
            cur += v
            prev = kind
        elif w in _TENS:
            if prev in ("unit", "teen", "tens"):
                return None
            cur += _TENS[w]
            prev = "tens"
        elif w == "hundred":
            if prev in ("hundred", "scale"):
                return None
            cur = (cur or 1) * 100
            prev = "hundred"
        else:
            total += (cur or 1) * _SCALES[w]
            cur = 0
            prev = "scale"
    return total + cur


def _words_to_digits(text: str, *, generous: bool) -> Tuple[str, List[Tuple[int, int, int, int]]]:
    """Rewrite number words as digits, returning the new text and the
    replacements as `(new_start, new_end, old_start, old_end)` so a claim can be
    reported as the user wrote it. `generous` (the evidence side) also reads
    `one`, `zero` and hyphenated compounds; the bullet side leaves them."""
    edits: List[Tuple[int, int, str]] = [(m.start(), m.end(), "1") for m in _A_SCALE.finditer(text)]
    for m in _WORD_RUN.finditer(text):
        run = m.group(0)
        if run.split()[0].lower() in _SCALES:
            continue                # `1 million`: the digit and scale word read natively
        if not generous:
            if run.lower() in ("one", "zero"):
                continue
            after, before = text[m.end():], text[max(0, m.start() - 1):m.start()]
            if (after.startswith("-") and not _COMPOUND_UNIT.match(after)) or before == "-":
                continue
        n = _parse_word_run(run)
        if n is not None:
            edits.append((m.start(), m.end(), str(n)))
    out, spans, last, shift = [], [], 0, 0
    for s, e, new in sorted(edits):
        if s < last:
            continue
        out.append(text[last:s])
        out.append(new)
        spans.append((s + shift, s + shift + len(new), s, e))
        shift += len(new) - (e - s)
        last = e
    out.append(text[last:])
    return "".join(out), spans


def _to_old(spans: List[Tuple[int, int, int, int]], pos: int, end: bool) -> int:
    """Map an offset in the digit-rewritten text back to the original."""
    delta = 0
    for ns, ne, os_, oe in spans:
        if ne <= pos:
            delta = oe - ne
        elif ns < pos or (ns == pos and not end):
            return oe if end else os_
    return pos + delta


_MONTHS = {m: i + 1 for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split())}
_DATE = re.compile(
    r"(?<![A-Za-z])(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+"
    r"(?:\d{1,2}(?:st|nd|rd|th)?,?\s+)?(?P<year>(?:19|20)\d{2})(?!\d)", re.I)
_RATIO = re.compile(r"(?<![\w./])\d+/\d+(?![\w/])")
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_CLAIM = re.compile(rf"(?<![A-Za-z0-9_./])(?<![A-Za-z]-)(?P<cur>[$€£]\s?)?(?P<num>{_NUM})")
_NEXT = re.compile(r"[\s-]?(?P<w>%|[A-Za-z×µ]+)")

_SCALE_WORDS = {"hundred": 100, "thousand": 10**3, "million": 10**6, "billion": 10**9, "trillion": 10**12,
                "k": 10**3, "mn": 10**6, "bn": 10**9}
_TIME_S = {"ms": Decimal("0.001"), "msec": Decimal("0.001"), "millisecond": Decimal("0.001"),
           "milliseconds": Decimal("0.001"),
           "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
           "min": 60, "mins": 60, "minute": 60, "minutes": 60,
           "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
           "d": 86400, "day": 86400, "days": 86400, "wk": 604800, "week": 604800,
           "weeks": 604800, "mo": 2629800, "month": 2629800, "months": 2629800,
           "yr": 31557600, "yrs": 31557600, "year": 31557600, "years": 31557600}
_BYTES = {"kb": 10**3, "mb": 10**6, "gb": 10**9, "tb": 10**12, "pb": 10**15,
          "kilobyte": 10**3, "kilobytes": 10**3, "megabyte": 10**6, "megabytes": 10**6,
          "gigabyte": 10**9, "gigabytes": 10**9, "terabyte": 10**12, "terabytes": 10**12}
_PERIODS = {"s": "second", "sec": "second", "second": "second", "min": "minute",
            "minute": "minute", "h": "hour", "hr": "hour", "hour": "hour", "day": "day",
            "wk": "week", "week": "week", "month": "month", "mo": "month", "year": "year",
            "yr": "year"}
_RATE = re.compile(r"\s?(?:(?P<noun>[A-Za-z]+)\s?)?(?:/|\bper\s+)\s?(?P<per>[A-Za-z]+)", re.I)
_RATE_ABBR = re.compile(r"\s?(?:qps|rps|tps|eps)\b", re.I)


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")


def extract_claims(text: str, *, generous: bool = False) -> List[Tuple[int, str, str]]:
    """Numeric claims in `text` as `(position, canonical key, surface)`.

    Keys make reformatting collapse: `1,000,000` = `1M` = `1 million` = `one
    million`; `2 years` = `24 months`; `40k requests/min` = `40,000 requests per
    minute`; `$5M` = `5 million dollars`. Kinds: `amt` (a bare number and money
    share it: `5M` supports `$5M`), `pct`, `time` (in seconds), `bytes`, `x`
    (multiplier), `rate:<period>`, `ord`, `ratio` and `date`. Position and
    surface refer to the original `text`. Sorted by position.
    """
    src, spans = _words_to_digits(text, generous=generous)
    found: List[Tuple[int, int, str]] = []            # (start, end, key) in `src`

    def blank(pattern, key_of):
        nonlocal src
        for m in list(pattern.finditer(src)):
            found.append((m.start(), m.end(), key_of(m)))
        src = pattern.sub(lambda m: " " * (m.end() - m.start()), src)

    # Dates and ratios first, blanked so their parts are not also bare numbers.
    blank(_DATE, lambda m: f"date:{m.group('year')}-{_MONTHS[m.group('mon')[:3].lower()]:02d}")
    blank(_RATIO, lambda m: f"ratio:{m.group(0)}")

    for m in _CLAIM.finditer(src):
        value = Decimal(m.group("num").replace(",", ""))
        end = m.end()
        nxt = _NEXT.match(src, end)
        if nxt:
            w = nxt.group("w")
            scale = None
            if w.lower() in _SCALE_WORDS:
                scale = _SCALE_WORDS[w.lower()]
            elif w in ("M", "B", "MM"):
                scale = 10**9 if w == "B" else 10**6
            if scale:
                value *= scale
                end = nxt.end()
        kind = "amt"
        nxt = _NEXT.match(src, end)
        w = nxt.group("w").lower() if nxt else ""
        rate = _RATE.match(src, end)
        abbr = _RATE_ABBR.match(src, end)
        attached = bool(nxt) and nxt.group(0)[0] not in " -"
        if w in ("%", "percent", "pct", "percentage"):
            kind, end = "pct", nxt.end()
        elif w in _BYTES:
            # `50GB/day` and `50GB of daily data` are one claim: the period is
            # not compared for data sizes.
            value *= _BYTES[w]
            kind, end = "bytes", nxt.end()
        elif rate and rate.group("per").lower() in _PERIODS and not m.group("cur"):
            kind, end = f"rate:{_PERIODS[rate.group('per').lower()]}", rate.end()
        elif abbr:
            kind, end = "rate:second", abbr.end()
        elif w in ("x", "×", "fold", "times") and not (
                w == "x" and src[nxt.end():nxt.end() + 1].isalpha()):
            kind, end = "x", nxt.end()
        elif w in _TIME_S and (len(w) > 1 or attached):
            value *= Decimal(_TIME_S[w])
            kind, end = "time", nxt.end()
        elif w in ("st", "nd", "rd", "th") and attached:
            kind, end = "ord", nxt.end()
        elif w in ("dollars", "dollar", "usd", "bucks"):
            end = nxt.end()
        found.append((m.start(), end, f"{kind}:{_fmt(value)}"))

    out = []
    for s, e, key in sorted(found):
        os_, oe = _to_old(spans, s, False), _to_old(spans, e, True)
        out.append((os_, key, text[os_:oe].strip()))
    return out


# Proper nouns and technology names ------------------------------------------
#
# What is flagged (each must be absent from the evidence, case-insensitively,
# after plural stripping, compound splitting and the alias groups below):
#   1. a known technology name from _TECH, in any case and any position;
#   2. an ALL-CAPS acronym that is not a generic one (_GENERIC_ACRONYMS);
#   3. a name with a capital inside it (PyTorch, GitHub, iOS) or with a digit or
#      symbol (S3, p99, GPT-4, C++, C#, Node.js, .NET);
#   4. a Capitalised word in mid-sentence.
# What is not: the first word of the bullet or of a sentence (`. ! ? : ;` or an
# opening bracket or quote before it), so a capitalised verb never trips it;
# any Capitalised word when the bullet is headline-cased (most content words
# capitalised, where the signal means nothing); single letters; and the generic
# capitalised words in _GENERIC_WORDS (months, seniority, role and org words).
# Precision over recall: a lowercase name outside _TECH ("we used kafka") is
# missed, and so is a fabricated employer that is not capitalised.

_TECH = frozenset("""
kafka docker kubernetes k8s terraform ansible jenkins airflow hadoop hive snowflake redshift
bigquery databricks dbt postgres postgresql mysql mongodb redis elasticsearch cassandra dynamodb
sqlite pytorch tensorflow keras sklearn scikit-learn xgboost lightgbm numpy pandas scipy
matplotlib seaborn tableau powerbi react angular vue svelte nextjs django fastapi graphql grpc
rabbitmq nginx aws gcp azure s3 ec2 kinesis langchain openai huggingface bert gpt llama git
github gitlab jira linux bash typescript javascript golang oracle salesforce sap kotlin
tensorrt onnx mlflow kubeflow prometheus grafana datadog splunk elk pyspark jupyter numba
sqlalchemy pytest junit selenium cypress webpack vite redux tailwind bootstrap flask
""".split())

_GENERIC_ACRONYMS = frozenset("""
api apis etl elt ml ai nlp ci cd qa ui ux kpi kpis roi okr okrs sla slas sdlc crud rest gui cli
sdk sdks oop mvp pr prs it hr us usa uk eu phd bs ms ba gpa ceo cto cfo faq seo b2b b2c saas
url http https json xml html css csv pdf sql dsa cv ip os ide llm llms gpu gpus cpu cpus ram
ocr eda etc vs pm hipaa gdpr soc pii tdd bi ab ok id ids vp svp devops mlops dataops arr mrr gmv cagr yoy
gb mb kb tb pb qps rps tps eps usd ms mm
""".split())

_GENERIC_WORDS = frozenset("""
i a an the and or of in on at to for with by as is are was were be it its this that these those
january february march april may june july august september october november december
monday tuesday wednesday thursday friday saturday sunday spring summer fall autumn winter
senior junior lead principal staff intern interns engineer engineers developer developers
manager director analyst scientist team teams data machine learning software cloud full stack
front end back systems system university college institute school department association club
society lab labs company group inc llc ltd corp research applied associate assistant head chief
president vice project projects program programs product products platform platforms
jan feb mar apr jun jul aug sep sept oct nov dec
""".split())

# Groups whose members support one another: if any member is in the evidence,
# all of them are.
_ALIASES = [
    ("js", "javascript"), ("ts", "typescript"), ("k8s", "kubernetes"),
    ("postgres", "postgresql", "psql"), ("mongo", "mongodb"), ("tf", "tensorflow"),
    ("sklearn", "scikit-learn", "scikit learn"), ("np", "numpy"), ("pd", "pandas"),
    ("node", "nodejs", "node.js"), ("react", "reactjs", "react.js"),
    ("vue", "vuejs", "vue.js"), ("golang", "go"), ("py", "python"),
    ("aws", "amazon web services"), ("gcp", "google cloud", "google cloud platform"),
    ("gh", "github"), ("bq", "bigquery"), ("pyspark", "spark"),
]

_TOKEN = re.compile(r"\.?[A-Za-z][A-Za-z0-9]*(?:[+#]+|(?:[.\-][A-Za-z0-9]+)*)")
_SENTENCE_START = re.compile(r"(?:^|[.!?:;(\[\"'“‘—–•*\-])\s*$")
_HEADLINE_FILLER = frozenset("and of the for with to in on at by a an or as".split())
_SYMBOLIC = re.compile(r"\d|[+#]|^\.|\.(?:js|net|io|py|ai)$", re.I)


def _singular(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _full_forms(token: str) -> set:
    """The lowercase spellings under which the whole of `token` is present."""
    t = token.lower().strip(".")
    return {f for f in (t, _singular(t), t.replace("-", ""), t.replace(".", ""),
                        t.replace("-", " ")) if f}


@lru_cache(maxsize=4096)
def _evidence_forms(text: str) -> "frozenset":
    """Every lowercase token form in `text` (whole tokens, their hyphen and dot
    parts, singulars), with the alias groups expanded."""
    low = text.lower()
    forms: set = set()
    for tok in _TOKEN.findall(text):
        forms |= _full_forms(tok)
        forms |= {_singular(p) for p in re.split(r"[-./]", tok.lower()) if len(p) > 1}
    for group in _ALIASES:
        if any((m in forms) or (" " in m and m in low) for m in group):
            for member in group:
                forms |= _full_forms(member)
                forms |= {p for p in member.split() if len(p) > 1}
    return frozenset(forms)


_DERIVED = re.compile(r"(?:ization|isation|izing|ising|ized|ised|ize|ise|ing|ed|ers|er|s)$")


def _supported_term(token: str, forms: "frozenset") -> bool:
    if _full_forms(token) & forms:
        return True
    # "Dockerized" asserts Docker, not a new name.
    root = _DERIVED.sub("", token.lower())
    if len(root) >= 4 and root != token.lower() and _full_forms(root) & forms:
        return True
    # A specific database supports the generic word ("PostgreSQL" -> "SQL").
    return token.lower() == "sql" and any(f.endswith("sql") or f.startswith("sql") for f in forms)


def _entity_candidates(text: str) -> List[Tuple[int, str]]:
    """`(position, name)` for each proper noun or technology name the bullet
    asserts, by the rules in the block comment above."""
    toks = list(_TOKEN.finditer(text))
    content = [m.group(0) for m in toks
               if len(m.group(0)) > 2 and m.group(0).lower() not in _HEADLINE_FILLER]
    # Headline case is judged on plain Capitalised words only: a bullet that
    # lists technologies ("Kafka, PyTorch, AWS") is not headline-cased.
    plain = sum(t[0].isupper() and t[1:].islower() and t.lower() not in _TECH for t in content)
    headline = len(content) >= 4 and plain / len(content) > 0.6
    shouting = len(content) >= 3 and sum(t.isupper() for t in content) / len(content) > 0.6
    out: List[Tuple[int, str]] = []
    for m in toks:
        tok = m.group(0)
        # `Kafka-based` asserts Kafka; `scikit-learn` and `GPT-4` are one name.
        name = tok if _SYMBOLIC.search(tok) or tok.lower() in _TECH else tok.split("-")[0]
        low = name.lower().strip(".")
        if len(low) < 2 and not re.search(r"[+#]", name):
            continue
        is_tech = low in _TECH
        if low in _GENERIC_WORDS or (low in _GENERIC_ACRONYMS and not is_tech):
            continue
        upper = name.isupper() and name.isalpha() and not shouting
        camel = bool(re.search(r"[a-z][A-Z]", name))
        symbolic = bool(_SYMBOLIC.search(name)) and (name[0].isalpha() or name[0] == ".")
        initial = _SENTENCE_START.search(text[:m.start()]) is not None
        capital = (name[0].isupper() and not name.isupper() and not initial and not headline)
        if is_tech or upper or camel or symbolic or capital:
            out.append((m.start(), name))
    return out


def unsupported_tokens(bullet: str, evidence: Sequence[str]) -> List[str]:
    """The tokens of `bullet` that `evidence` (a list of source texts) does not
    support: numbers, dates, durations, money and scale, and proper nouns and
    technology names, in order of appearance, each once, as the bullet wrote
    them. Empty when the bullet is fully supported."""
    joined = " \n ".join(e for e in evidence if e)
    have = {k for _, k, _ in extract_claims(joined, generous=True)}
    forms = _evidence_forms(joined)
    found: List[Tuple[int, str, str]] = [
        (pos, key, surface) for pos, key, surface in extract_claims(bullet) if key not in have]
    found += [(pos, tok.lower(), tok) for pos, tok in _entity_candidates(bullet)
              if not _supported_term(tok, forms)]
    out, seen = [], set()
    for _, key, surface in sorted(found):
        if key not in seen:
            seen.add(key)
            out.append(surface)
    return out


def cite_evidence(cite: str, source_bullets: Dict[str, List[str]]) -> List[str]:
    """The text a cite (`<key>#b<n>`, or a bare `<key>`) points at."""
    cite = (cite or "").strip().lower()
    key, sep, idx = cite.rpartition("#b")
    if sep and idx.isdigit() and key in source_bullets:
        rows = source_bullets[key]
        return [rows[int(idx)]] if int(idx) < len(rows) else []
    if cite in source_bullets:
        return list(source_bullets[cite])
    # A skill, or an item with no source bullets: the name is all the evidence.
    return [cite.split(":", 1)[-1].replace("|", " ")] if ":" in cite else []


def consistency_check(content: Dict, source_bullets: Dict[str, List[str]],
                      approved: Optional[Dict[str, Iterable[str]]] = None,
                      variant_evidence: Optional[Dict[Tuple[str, str], str]] = None,
                      ) -> List[Tuple[str, str, List[str]]]:
    """`(item key, bullet, unsupported tokens)` for every experience and project
    bullet that asserts something its evidence does not.

    A bullet is checked against its **cited** evidence only: the text of the
    source bullets its cites resolve to, plus its own item's source bullets and
    header (title, company, name, dates). Never the whole profile. A bullet that
    is verbatim one of its item's source bullets is skipped, and so is a bullet
    with no cites (the citations gate's job). `approved` (#229) maps an item key
    to the normalized text of its approved library variants: a bullet verbatim
    one is the user's own confirmed wording and is skipped the same way.
    `variant_evidence` maps `(item key, normalized bullet)` to the approved variant that bullet
    names (`from_variant`): its text joins that bullet's evidence, so a number or name the
    user approved in the variant is not flagged. A bullet naming none gets nothing extra.
    """
    out: List[Tuple[str, str, List[str]]] = []
    for section, key_fn in (("experiences", exp_key), ("projects", proj_key)):
        for item in content.get(section) or []:
            key = key_fn(item)
            own = source_bullets.get(key, [])
            own_norm = {" ".join(b.split()) for b in own} | set((approved or {}).get(key, ()))
            cites = item.get("cites") if isinstance(item.get("cites"), dict) else {}
            header = [str(item.get(f) or "") for f in
                      ("title", "company", "name", "start_date", "end_date")]
            for b in item.get("bullets") or []:
                named = cites.get(b) or []
                if not (b or "").strip() or not named or " ".join(b.split()) in own_norm:
                    continue
                evidence = header + list(own)
                for c in named:
                    evidence += cite_evidence(c, source_bullets)
                named_variant = (variant_evidence or {}).get((key, " ".join(b.split())))
                if named_variant:
                    evidence.append(named_variant)
                bad = unsupported_tokens(b, evidence)
                if bad:
                    out.append((key, b, bad))
    return out
