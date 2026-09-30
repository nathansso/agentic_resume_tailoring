"""The negative-pin check (#232): does a changed bullet mention a pinned topic, in other words?

A negative pin (#198) is a fact that must never render. `acceptance.negative_pin_violations`
matches the pinned *term* as a whole word, so "message broker" pinned and "Kafka" written,
or "Amazon" pinned and "AWS" written, reach the page. This asks Jev one yes/no question per
(text, pin) and turns a confident yes into a `preferences` gate violation in the same format
as the term match. The term match is unchanged and always runs; Jev only adds hits.

**Question.** One `noul`, `negative_pin@v1`, built per pin and worded positively: "Does
this text mention or refer to <topic>?". Never "does it avoid...": Jev is documented as weak
on negation and reads literally. A pin's user statement is the natural description of its
topic, but it is often a directive ("Never mention Kafka"), so `pin_topic` strips the leading
directive, and falls back to the bare term when what is left still carries a negation or a
comparison ("Don't mention Kafka, I used it less than Airflow"), so the negation never reaches
Jev. All of a text's pins go in one request, with the text as the state.

**State** is `{text, kind}`: the bullet (or the item field: title, company, name,
description) and which of those it is. Nothing else from the resume or the knowledge graph.

**What is checked.** Only text that changed against the base version: a bullet the base
item already had, or an unchanged item field, is skipped. A text that the term match already
catches is skipped too (it is a violation regardless, and needs no call). With no key, mode
`off`, or an API error the finding is `unchecked` and adds nothing.

**Score and thresholds.** A finding's score is Jev's yes-probability. Its confidence is not
claimed to be calibrated, so the tiers are cutoffs fitted on a labelled set
(`eval/negative_pin_labels/`), not probabilities: act (`p >= TAU_BLOCK`: a gate violation),
verify (`p >= TAU_REVIEW`: shown to the host as `review`), and everything else passes.

Model-free apart from `engine.decide`, imported only when a check runs.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from agents.checks import exp_key, proj_key
from harness.decisions.questions import Noul, canonical

POINT = "negative_pin"
VERSION = "negative_pin@v1"
FIELDS = ("title", "company", "name", "description")
LABEL = "mentions"

# Fitted (#232) on the 74 proposed pairs in eval/negative_pin_labels/, against jev-1.13.0's recorded
# answers to negative_pin@v1, scoring by Jev's yes-probability. Refit with
# `python eval/fit_negative_pin_threshold.py analyze` whenever the model or the question changes.
#   TAU_BLOCK 0.85: 0 of 33 not-a-mention pairs blocked; the highest of them is n_hadoop_spark, a
#     near-miss at 0.76 (0.09 of headroom), and the highest unrelated pair scores 0.05. Blocks 38 of 41
#     mentions (93%, precision 100%): direct 11/11, paraphrase 14/14, indirect 7/10.
#   TAU_REVIEW 0.15: the band [0.15, 0.85) holds 4 pairs (3 mentions: i_ms_github 0.27,
#     i_employer_field 0.42, i_meta_react 0.57; 1 not: n_hadoop_spark); no mention passes silently.
#     The highest score under the band is 0.13 (n_rl_replay, a near-miss).
TAU_BLOCK = 0.85
TAU_REVIEW = 0.15

_ROUND = 4
TOPIC_MAX_CHARS = 120

# A leading directive is not part of the topic: "Never mention Kafka" is about Kafka.
_DIRECTIVE = re.compile(
    r"^\s*(?:please\s+)?"
    r"(?:(?:never|do\s+not|don['’]?t|must\s+not|should\s+not|shouldn['’]?t|no)\s+(?:ever\s+)?)?"
    r"(?:mention(?:ing)?|include|including|reference|referencing|list|listing|show|showing|put|"
    r"use|using|bring\s+up|talk\s+about|write\s+about|refer\s+to|cite|add|highlight|feature|"
    r"emphasi[sz]e|avoid|skip|omit|hide|exclude|drop|leave\s+out|keep\s+out)"
    r"(?:\s+(?:mentioning|including|referencing|using))?"
    r"(?:\s+(?:anything|any\s+mention|any\s+reference|any\s+talk)\s+(?:about|of|to|on|regarding|"
    r"related\s+to))?\s+",
    re.IGNORECASE)
# What is still in the statement after the directive that Jev must not see.
_NEGATION = re.compile(
    r"\b(?:not|no|never|nor|neither|without|nothing|none|only|rather|instead|than|unlike|"
    r"less|more|but|except|besides|avoid|skip|omit|hide|exclude|isn|aren|wasn|weren|don|doesn|"
    r"didn|won|wouldn|shouldn|cannot)\b|n['’]t\b",
    re.IGNORECASE)


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _short(text: str, n: int = 60) -> str:
    text = _norm(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def term_pattern(term: str) -> "re.Pattern":
    """`term` as a whole word or phrase, case-insensitive."""
    return re.compile(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])")


def pin_topic(term: str, statement: Optional[str] = None) -> str:
    """What the question asks about: the pin's statement without its leading directive,
    when that is a clean description, else the term."""
    term = _norm(term)
    text = _norm(statement or "")
    if not text:
        return term
    stripped = _DIRECTIVE.sub("", text, count=1).strip().rstrip(".!;,")
    if not stripped or len(stripped) > TOPIC_MAX_CHARS or _NEGATION.search(stripped):
        return term
    return stripped


def question_for(topic: str) -> Noul:
    """The one question, for one topic. Rewording it means bumping `VERSION`."""
    return Noul(
        VERSION,
        f"Does this text mention or refer to {topic}? Answer yes when it names it, uses another "
        "name for it, names a tool, product, employer or project that belongs to it, or "
        "describes it in other words. A topic that is only related, or of the same kind, "
        "is not a mention.",
        true="The text mentions or refers to the topic.",
        false="The text does not refer to the topic; it may concern something related.")


def build_state(text: str, kind: str = "bullet") -> Dict[str, Any]:
    return {"text": _norm(text), "kind": kind}


def _page_items(content: Dict):
    for section, key_fn in (("experiences", exp_key), ("projects", proj_key)):
        for item in content.get(section) or []:
            yield key_fn(item), item


def _changed_texts(content: Dict, base: Optional[Dict]):
    """`(item key, kind, text)` for every bullet and item field that is not in the base."""
    base_items = dict(_page_items(base)) if base is not None else None
    for key, item in _page_items(content):
        old = base_items.get(key) if base_items is not None else None
        old_bullets = {_norm(b) for b in (old or {}).get("bullets") or []}
        for kind in FIELDS:
            value = _norm(str(item.get(kind) or ""))
            if value and (old is None or value != _norm(str((old or {}).get(kind) or ""))):
                yield key, kind, value
        for bullet in item.get("bullets") or []:
            if (bullet or "").strip() and _norm(bullet) not in old_bullets:
                yield key, "bullet", bullet


def make_pin_checker(pins: Sequence[Dict[str, Any]],
                     base_content: Optional[Dict] = None) -> Callable[[Dict], List[Dict]]:
    """`content -> findings`, one per (changed text, pin) that needs checking.

    `pins` is `[{"term", "statement"}]`. A finding is `{item, kind, text, pin, topic, status,
    p, source, reason}`: `status` is `checked` (Jev or the cache answered) or `unchecked` (the
    fallback did; `reason` says why). Results are memoized for the life of the checker, so the
    many metric vectors one plan computes ask each text once.
    """
    entries = []
    for pin in sorted(pins, key=lambda p: str(p.get("term") or "").lower()):
        term = _norm(str(pin.get("term") or "")).lower()
        if term:
            topic = pin_topic(term, pin.get("statement"))
            entries.append((term, term_pattern(term), topic, question_for(topic)))
    memo: Dict[str, List[Dict]] = {}

    def check(content: Dict) -> List[Dict]:
        from harness.decisions import engine

        findings: List[Dict] = []
        if not entries:
            return findings
        for key, kind, text in _changed_texts(content, base_content):
            state = build_state(text, kind)
            memo_key = canonical(state)
            if memo_key not in memo:
                todo = [e for e in entries if not e[1].search(text.lower())]   # the term match owns those
                answers = engine.decide(POINT, state, [e[3] for e in todo], fallback=None) if todo else []
                memo[memo_key] = [_finding(term, topic, kind, text, ans)
                                  for (term, _pat, topic, _q), ans in zip(todo, answers)]
            findings += [{**f, "item": key} for f in memo[memo_key]]
        return findings

    return check


def _finding(term: str, topic: str, kind: str, text: str, answer) -> Dict:
    base = {"kind": kind, "text": text, "pin": term, "topic": topic}
    if answer.fell_back:
        return {**base, "status": "unchecked", "p": None, "source": "fallback",
                "reason": answer.reason or "fallback"}
    return {**base, "status": "checked", "p": round(float(answer.p), _ROUND),
            "source": answer.source, "reason": None, "model": answer.model}


def _where(f: Dict) -> str:
    return f["kind"] if f["kind"] != "bullet" else f'"{_short(f["text"])}"'


def violations(findings: Iterable[Dict]) -> List[str]:
    """Gate violations for findings scoring >= TAU_BLOCK, in the term match's own format
    (`negative_pin:<term>@<key> :: "<bullet>"`, or `:: <field>`). Sorted."""
    return sorted({f'negative_pin:{f["pin"]}@{f["item"]} :: {_where(f)}'
                   for f in findings if f.get("status") == "checked" and f["p"] >= TAU_BLOCK})


def reviews(findings: Iterable[Dict]) -> List[Dict]:
    """The uncertain band for the host to show the user: TAU_REVIEW <= p < TAU_BLOCK.
    Entries carry `check: negative_pin` so they sit beside the support check's in one list."""
    out = [{"check": "negative_pin", "item": f["item"], "where": f["kind"],
            "bullet": _short(f["text"], 80), "pin": f["pin"], "label": LABEL, "p": f["p"]}
           for f in findings if f.get("status") == "checked" and TAU_REVIEW <= f["p"] < TAU_BLOCK]
    return sorted(out, key=lambda r: (r["item"], r["where"], r["bullet"], r["pin"]))
