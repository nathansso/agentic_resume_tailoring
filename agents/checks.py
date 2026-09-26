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
from typing import Any, Dict, List, Optional, Tuple

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
# Achievements are not calibrated — the
# formatter's achievements block does not compile under tectonic today (a bare
# `\resumeItemListStart` directly inside `\resumeSubHeadingListStart` is a
# "missing \item" error) — so they reuse the experience section and list
# constants.
MAX_BULLET_LINES = 2
PAGE_LINE_BUDGET = 60

LINE_COSTS: Dict[str, float] = {
    "header": 3.30,              # page top + name + contact line
    "section:education": 1.65,   # \section rule + outer list, per present section
    "section:experience": 2.16,
    "section:projects": 1.93,
    "section:skills": 3.18,
    "section:achievements": 2.16,  # uncalibrated, see above
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
