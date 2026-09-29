"""`decide`: the one way ART asks Jev anything (#193).

    answers = decide("support", state, [question, ...], fallback)

For one state and its questions it:

1. looks up **every** question in the cache (`cache.py`, keyed per question);
2. sends **only the misses**, in one batched request for that state (questions
   in a request are answered independently, so batching costs no accuracy and
   sends the state once);
3. caches each answer with the model version that actually answered.

`ART_JEV_MODE` decides how far it goes:

- `off`: never the cache, never the API; always the fallback. The privacy
  switch: JD text and resume bullets leave the machine only in Jev requests.
- `replay`: the cache only. A miss raises `JevReplayMiss`; it never calls the
  API and never falls back, so a stale recording fails loudly (the semantics of
  `eval/cassettes.py`).
- `auto` (default): cache, then Jev when a key is set, then the fallback on no
  key or any API error.

An unrecognized value is treated as `off`: a typo must not send text anywhere.

Every `Answer` says where it came from (`cache`, `jev`, or `fallback` with a
`reason`), and `stats()` counts them per decision point. Nothing here decides
what an answer *means*: thresholds belong to the caller, fitted on ART's data,
never taken from Jev's reported confidence.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from harness.decisions import cache
from harness.decisions.client import (
    MAX_QUESTION_TOKENS, MAX_REQUEST_TOKENS, JevClient, JevError, JevReplayMiss,
    configured_model, resolve_api_key,
)
from harness.decisions.questions import Answer, AnswerError, Question, canonical, estimate_tokens

log = logging.getLogger(__name__)

MODE_ENV = "ART_JEV_MODE"
MODES = ("off", "replay", "auto")

Fallback = Union[None, Any, Callable[[Question], Any]]

_lock = threading.Lock()
_stats: Dict[str, Dict[str, Any]] = {}


def mode() -> str:
    raw = (os.environ.get(MODE_ENV) or "auto").strip().lower()
    if raw in MODES:
        return raw
    log.warning("%s=%r is not one of %s; treating it as 'off'", MODE_ENV, raw, MODES)
    return "off"


def get_client() -> Optional[JevClient]:
    """A client when a key is available, else None. Tests replace this."""
    key = resolve_api_key()
    return JevClient(key, model=configured_model()) if key else None


# ── stats ────────────────────────────────────────────────────────────────────

def _bump(point: str, field: str, n: float = 1, reason: Optional[str] = None) -> None:
    with _lock:
        row = _stats.setdefault(point, {"cache": 0, "jev": 0, "fallback": 0, "replay_miss": 0,
                                        "requests": 0, "input_tokens": 0.0,
                                        "output_tokens": 0.0, "reasons": {}})
        row[field] += n
        if reason:
            row["reasons"][reason] = row["reasons"].get(reason, 0) + n


def stats() -> Dict[str, Dict[str, Any]]:
    """Per decision point: answers by source, requests sent, tokens billed, the
    fallback reasons, and `hit_rate` (cache answers over all answers)."""
    with _lock:
        out = {}
        for point, row in _stats.items():
            total = row["cache"] + row["jev"] + row["fallback"]
            out[point] = {**row, "reasons": dict(row["reasons"]),
                          "hit_rate": round(row["cache"] / total, 4) if total else None}
        return out


def reset_stats() -> None:
    with _lock:
        _stats.clear()


# ── decide ───────────────────────────────────────────────────────────────────

def _fallback_answer(question: Question, fallback: Fallback, reason: str) -> Answer:
    value = fallback(question) if callable(fallback) else fallback
    return Answer.fallback(question.kind, value, reason)


def _chunks(state_tokens: int, misses: List):
    """Split misses so each request stays within the documented token limit."""
    chunk, used = [], state_tokens
    for item in misses:
        size = estimate_tokens(canonical(item[1].wire()))
        if chunk and used + size > MAX_REQUEST_TOKENS:
            yield chunk
            chunk, used = [], state_tokens
        chunk.append(item)
        used += size
    if chunk:
        yield chunk


def decide(point: str, state: Any, questions: Sequence[Question], fallback: Fallback = None,
           *, client: Optional[JevClient] = None,
           mode_override: Optional[str] = None) -> List[Answer]:
    """One `Answer` per question, in order. See the module docstring."""
    questions = list(questions)
    for q in questions:
        if q.point != point:
            raise ValueError(f"question {q.version} does not belong to decision point {point!r}")
    run_mode = mode_override or mode()
    if run_mode == "off":
        for _ in questions:
            _bump(point, "fallback", reason="mode=off")
        return [_fallback_answer(q, fallback, "mode=off") for q in questions]

    model = client.model if client else configured_model()
    keys = [cache.cache_key(state, q, model) for q in questions]
    rows = cache.get_many(keys)
    answers: Dict[int, Answer] = {}
    for i, (key, q) in enumerate(zip(keys, questions)):
        row = rows.get(key)
        if row is None:
            continue
        try:
            answers[i] = q.parse(row.answer, model=row.resolved_model, source="cache")
        except AnswerError as exc:                   # a corrupt row is a miss
            log.warning("jev cache: discarding unreadable row %s: %s", key[:12], exc)
            continue
        _bump(point, "cache")

    missing = [i for i in range(len(questions)) if i not in answers]
    if missing and run_mode == "replay":
        for _ in missing:
            _bump(point, "replay_miss")
        raise JevReplayMiss(
            f"{point}: {len(missing)} of {len(questions)} decisions are not in the cache "
            f"({', '.join(sorted({questions[i].version for i in missing}))}; "
            f"key {keys[missing[0]][:12]}). Replay never calls the API: re-record with "
            f"{MODE_ENV}=auto, or run `art jev import`.")

    if missing:
        _ask(point, state, questions, keys, missing, answers, fallback, client, model)
    return [answers[i] for i in range(len(questions))]


def _ask(point, state, questions, keys, missing, answers, fallback, client, model) -> None:
    def give_up(indices, reason):
        for i in indices:
            answers[i] = _fallback_answer(questions[i], fallback, reason)
            _bump(point, "fallback", reason=reason)

    client = client or get_client()
    if client is None:
        return give_up(missing, "no_key")

    state_tokens = estimate_tokens(canonical(state))
    sendable = []
    for i in missing:
        if state_tokens + estimate_tokens(canonical(questions[i].wire())) > MAX_QUESTION_TOKENS:
            give_up([i], "too_large")
        else:
            sendable.append(i)
    # Identical (state, question) pairs in one call are one question.
    unique: Dict[str, int] = {}
    for i in sendable:
        unique.setdefault(keys[i], i)
    batch = [(keys[i], questions[i]) for i in unique.values()]

    for chunk in _chunks(state_tokens, batch):
        ids = {f"q{n}": (key, q) for n, (key, q) in enumerate(chunk)}
        try:
            resp = client.ask(state, {qid: q for qid, (_, q) in ids.items()})
        except JevError as exc:
            log.warning("jev request failed (%s); using the fallback", exc.code)
            for key, _q in chunk:
                give_up([i for i in sendable if keys[i] == key], f"api_error:{exc.code}")
            continue
        _bump(point, "requests")
        usage_in = float((resp.usage or {}).get("input_tokens") or 0) / len(chunk)
        usage_out = float((resp.usage or {}).get("output_tokens") or 0) / len(chunk)
        _bump(point, "input_tokens", usage_in * len(chunk))
        _bump(point, "output_tokens", usage_out * len(chunk))
        if resp.model and resp.model != model:
            log.info("jev: asked for %s, answered by %s", model, resp.model)
        rows = []
        for qid, (key, q) in ids.items():
            raw = resp.answers.get(qid)
            try:
                parsed = q.parse(raw, model=resp.model or model, source="jev")
            except AnswerError as exc:
                log.warning("jev: unusable answer for %s: %s", q.version, exc)
                for i in [i for i in sendable if keys[i] == key]:
                    answers[i] = _fallback_answer(q, fallback, "bad_answer")
                    _bump(point, "fallback", reason="bad_answer")
                continue
            rows.append(cache.make_row(key, q, raw, requested_model=model,
                                       resolved_model=resp.model or model,
                                       input_tokens=usage_in, output_tokens=usage_out))
            for i in [i for i in sendable if keys[i] == key]:
                answers[i] = parsed
                _bump(point, "jev")
        cache.put_many(rows)
