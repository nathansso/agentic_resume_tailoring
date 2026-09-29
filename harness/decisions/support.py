"""The cited-bullet support check (#193), the first Jev decision point.

The `citations` gate (#198) proves a bullet cites real evidence, not that the
bullet says what the evidence says; lexical drift (`agents.checks.
faithfulness_drift`) only measures word overlap. This asks Jev, per revised
bullet: how does the *cited* evidence relate to the new text?

    supported         every claim is stated or directly implied by the evidence
    adds_unsupported  claims something the evidence does not state (a larger
                      role, a scope, a tool, an outcome)
    contradicts       the evidence says otherwise

**Direction.** Evidence to bullet is the hard direction: the bullet may claim
nothing beyond its cited evidence. Added specificity is fine when a cite
grounds it ("built ETL pipelines using Airflow" passes when a cited bullet
names Airflow), and dropped detail is not checked here (the guards own it).
Numbers are #123's regex gate, not this.

**State** is narrow: `{evidence, original?, bullet}`. Evidence is the text of
the cited source bullets (`<key>#b<n>`, or an item key for all its bullets) and
nothing else from the knowledge graph.

**What is checked.** Only bullets that changed: a verbatim source bullet is
skipped (which also keeps a user's own editor edits from being blocked) and so
is an uncited bullet (the citations gate owns it). With no key, mode `off`, or
an API error the finding is `unchecked` and adds nothing.

**Thresholds.** `TAU_BLOCK` and `TAU_REVIEW` are provisional (0.8 from
TypeSafe's cookbook) until they are fitted on a hand-labelled set. Jev's
confidence is not claimed to be calibrated, so the three tiers are: act
(`p >= TAU_BLOCK`: a gate violation), verify (`p >= TAU_REVIEW`: shown to the
host as `review`), and everything else passes.

This module is model-free apart from `engine.decide`, which is imported only
when a check runs.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from agents.checks import exp_key, proj_key
from harness.decisions.questions import Choice, canonical

QUESTION = Choice(
    "support@v1",
    "How does the evidence relate to the new bullet? Judge only what the bullet claims "
    "about the candidate's own work. The evidence is the only ground truth; `original` is "
    "the bullet being revised, shown for context, and is not evidence.",
    {
        "supported": "Every claim in the bullet is stated or directly implied by the evidence.",
        "adds_unsupported": "The bullet claims something the evidence does not state, such as "
                            "a larger role, a scope, a tool or an outcome.",
        "contradicts": "The evidence says otherwise.",
    })

LABELS = tuple(QUESTION.options)
BLOCKING = {"contradicts": "contradicts", "adds_unsupported": "unsupported"}

# Provisional. Fit on a hand-labelled set of (evidence, bullet) pairs before
# relying on either (see the #193 PR); never derived from Jev's confidence.
TAU_BLOCK = 0.8
TAU_REVIEW = 0.4

_ROUND = 4
ORIGINAL_MIN_OVERLAP = 0.25
_WORD = re.compile(r"[a-z0-9]+")


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _short(text: str, n: int = 60) -> str:
    text = _norm(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def _tokens(text: str) -> set:
    return set(_WORD.findall((text or "").lower()))


def resolve_evidence(cites: Sequence[str], source_bullets: Dict[str, List[str]]) -> List[str]:
    """The text a bullet's cites point at: `<key>#b<n>` is that source bullet, an
    exp:/proj: key is all of that item's source bullets. Other cites (skills,
    education) name no bullet text. Sorted and de-duplicated, so the cache key
    does not depend on the order the host listed them."""
    found: Dict[Tuple[str, int], str] = {}
    for cite in cites or []:
        cite = (cite or "").strip().lower()
        key, sep, idx = cite.rpartition("#b")
        bullets = source_bullets.get(key if sep else cite)
        if not bullets:
            continue
        if sep and idx.isdigit():
            if int(idx) < len(bullets):
                found[(key, int(idx))] = _norm(bullets[int(idx)])
        elif not sep:
            for n, b in enumerate(bullets):
                found[(cite, n)] = _norm(b)
    return [found[k] for k in sorted(found)]


def _original(bullet: str, base_bullets: Sequence[str]) -> Optional[str]:
    """The base version's bullet this one most likely revises: the most similar
    by word overlap, when similar enough to be a revision at all."""
    mine, best, best_score = _tokens(bullet), None, 0.0
    for b in base_bullets:
        if _norm(b) == _norm(bullet):
            continue
        theirs = _tokens(b)
        union = mine | theirs
        score = len(mine & theirs) / len(union) if union else 0.0
        if score > best_score:
            best, best_score = b, score
    return _norm(best) if best is not None and best_score >= ORIGINAL_MIN_OVERLAP else None


def build_state(evidence: Sequence[str], bullet: str, original: Optional[str] = None) -> Dict[str, Any]:
    state: Dict[str, Any] = {"evidence": list(evidence), "bullet": _norm(bullet)}
    if original:
        state["original"] = original
    return state


def _page_items(content: Dict):
    for section, key_fn in (("experiences", exp_key), ("projects", proj_key)):
        for item in content.get(section) or []:
            yield key_fn(item), item


def make_support_checker(source_bullets: Dict[str, List[str]],
                         base_content: Optional[Dict] = None) -> Callable[[Dict], List[Dict]]:
    """`content -> findings`, one per bullet that needs checking.

    A finding is `{item, bullet, status, label, p, probabilities, source,
    reason}`: `status` is `checked` (Jev or the cache answered), or `unchecked`
    (the fallback did, `reason` says why). Results are memoized for the life of
    the checker, so the many metric vectors one plan computes ask each bullet
    once (and the engine's cache makes a rerun ask nothing).
    """
    base = {key: [b for b in item.get("bullets") or [] if b]
            for key, item in _page_items(base_content or {})}
    memo: Dict[str, Dict] = {}

    def check(content: Dict) -> List[Dict]:
        from harness.decisions import engine

        findings: List[Dict] = []
        for key, item in _page_items(content):
            sources = {_norm(b) for b in source_bullets.get(key, [])}
            cites = item.get("cites") if isinstance(item.get("cites"), dict) else {}
            for bullet in item.get("bullets") or []:
                if not (bullet or "").strip() or _norm(bullet) in sources:
                    continue                                   # verbatim: nothing to check
                named = cites.get(bullet) or []
                if not named:
                    continue                                   # uncited: the citations gate's
                evidence = resolve_evidence(named, source_bullets)
                if not evidence:
                    findings.append(_unchecked(key, bullet, "no_evidence_text"))
                    continue
                state = build_state(evidence, bullet, _original(bullet, base.get(key, [])))
                memo_key = canonical(state)
                if memo_key not in memo:
                    answer = engine.decide("support", state, [QUESTION],
                                           fallback=None)[0]
                    memo[memo_key] = _finding(key, bullet, answer)
                findings.append({**memo[memo_key], "item": key, "bullet": bullet})
        return findings

    return check


def _unchecked(key: str, bullet: str, reason: str) -> Dict:
    return {"item": key, "bullet": bullet, "status": "unchecked", "label": None, "p": None,
            "probabilities": None, "source": "fallback", "reason": reason}


def _finding(key: str, bullet: str, answer) -> Dict:
    if answer.fell_back:
        return _unchecked(key, bullet, answer.reason or "fallback")
    probs = {k: round(float(v), _ROUND) for k, v in sorted((answer.probabilities or {}).items())}
    return {"item": key, "bullet": bullet, "status": "checked", "label": answer.value,
            "p": None if answer.p is None else round(float(answer.p), _ROUND),
            "probabilities": probs or None, "source": answer.source, "reason": None,
            "model": answer.model}


def _worst(finding: Dict) -> Tuple[Optional[str], float]:
    """The blocking label with the most probability behind it, and that p."""
    probs = finding.get("probabilities") or {}
    label = max(BLOCKING, key=lambda l: (probs.get(l, 0.0), l))
    p = probs.get(label)
    if p is None and finding.get("label") == label:      # no probabilities: use the pick's p
        p = finding.get("p")
    return (label, float(p)) if p is not None else (None, 0.0)


def violations(findings: Sequence[Dict]) -> List[str]:
    """Gate violations: `contradicts:` / `unsupported:` plus the item and bullet,
    for findings whose worst label has p >= TAU_BLOCK. Sorted."""
    out = []
    for f in findings:
        if f.get("status") != "checked":
            continue
        label, p = _worst(f)
        if label and p >= TAU_BLOCK:
            out.append(f'{BLOCKING[label]}:{f["item"]}: "{_short(f["bullet"])}"')
    return sorted(set(out))


def reviews(findings: Sequence[Dict]) -> List[Dict]:
    """The uncertain band, for the host to show the user: TAU_REVIEW <= p <
    TAU_BLOCK on a blocking label. Sorted by item then bullet."""
    out = []
    for f in findings:
        if f.get("status") != "checked":
            continue
        label, p = _worst(f)
        if label and TAU_REVIEW <= p < TAU_BLOCK:
            out.append({"item": f["item"], "bullet": _short(f["bullet"], 80), "label": label,
                        "p": round(p, _ROUND)})
    return sorted(out, key=lambda r: (r["item"], r["bullet"], r["label"]))
