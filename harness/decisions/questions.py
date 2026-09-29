"""Typed questions for Jev, their canonical form, and normalized answers (#193).

The wire format is the documented `POST /v1/systemone` body
(docs.typesafe.ai/api): `questions` is a map of user-chosen ids to
`{type, instructions, criteria}`.

- `Noul`: a yes-probability. Optional `criteria.true` / `criteria.false`.
- `Choice`: one of up to 255 options, each with a description or `null`. A
  `no_match` option (a `null`-described catch-all) is for where "none of these"
  is a real answer.
- `Score`: an ordered scale of 2-10 levels.

**Every question carries a version** named for its decision point
(`support@v1`). The version is part of the canonical form, so rewording a
question and bumping its version visibly invalidates its cached decisions and
its recordings instead of silently answering a different question.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

MAX_OPTIONS = 255
MIN_LEVELS, MAX_LEVELS = 2, 10
_VERSION = re.compile(r"^[a-z][a-z0-9_]*@v\d+$")

SOURCES = ("cache", "jev", "fallback")


class QuestionError(ValueError):
    """A question that breaks a documented limit; raised when it is built."""


class AnswerError(ValueError):
    """A response answer that does not match the question it answers."""


def canonical(obj: Any) -> str:
    """Stable JSON: sorted keys, no whitespace, non-ASCII kept. What gets hashed."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _num(x: Any) -> Optional[float]:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        return None
    return float(x)


@dataclass
class Answer:
    """One answer, whatever its type and wherever it came from.

    `value` is the yes-probability (noul), the chosen option (choice) or the
    score (score). `p` is the probability behind it: the yes-probability, the
    chosen option's probability, or the score's confidence. Never compare `p`
    across questions or batches: only options within one question compare.
    """
    kind: str
    value: Any
    p: Optional[float] = None
    probabilities: Optional[Dict[str, float]] = None
    confidence: Optional[float] = None
    source: str = "jev"                      # cache | jev | fallback
    reason: Optional[str] = None             # why, when source == "fallback"
    model: Optional[str] = None              # the version that answered

    def p_of(self, option: str) -> float:
        return float((self.probabilities or {}).get(option, 0.0))

    @property
    def fell_back(self) -> bool:
        return self.source == "fallback"

    @classmethod
    def fallback(cls, kind: str, value: Any, reason: str) -> "Answer":
        return cls(kind=kind, value=value, source="fallback", reason=reason)


class Question:
    kind = ""

    def __init__(self, version: str, instructions: Any):
        if not isinstance(version, str) or not _VERSION.match(version):
            raise QuestionError(f"version must look like 'point@v1', got {version!r}")
        if not instructions:
            raise QuestionError("a question needs instructions")
        self.version = version
        self.instructions = instructions

    @property
    def point(self) -> str:
        return self.version.partition("@")[0]

    def wire(self) -> Dict[str, Any]:
        raise NotImplementedError

    def canonical(self) -> str:
        return canonical({"version": self.version, "question": self.wire()})

    def parse(self, raw: Any, model: Optional[str] = None, source: str = "jev") -> Answer:
        raise NotImplementedError

    def _check_type(self, raw: Any) -> Dict[str, Any]:
        if not isinstance(raw, Mapping) or raw.get("type") != self.kind:
            raise AnswerError(f"expected a {self.kind} answer, got {raw!r}")
        return dict(raw)


class Noul(Question):
    kind = "noul"

    def __init__(self, version: str, instructions: Any,
                 true: Optional[str] = None, false: Optional[str] = None):
        super().__init__(version, instructions)
        self.true, self.false = true, false

    def wire(self):
        out: Dict[str, Any] = {"type": "noul", "instructions": self.instructions}
        criteria = {k: v for k, v in (("true", self.true), ("false", self.false)) if v}
        if criteria:
            out["criteria"] = criteria
        return out

    def parse(self, raw, model=None, source="jev"):
        raw = self._check_type(raw)
        p = _num(raw.get("noul"))
        if p is None or not 0.0 <= p <= 1.0:
            raise AnswerError(f"noul answer out of range: {raw.get('noul')!r}")
        # A noul answer has no confidence and P(yes)+P(no) is not guaranteed to
        # be 1, so only the yes-probability is kept.
        return Answer("noul", p, p=p, source=source, model=model)


class Choice(Question):
    kind = "choice"

    def __init__(self, version: str, instructions: Any,
                 options: Mapping[str, Optional[str]], no_match: Optional[str] = None):
        super().__init__(version, instructions)
        options = dict(options)
        if no_match is not None:
            if no_match in options:
                raise QuestionError(f"no_match {no_match!r} duplicates an option")
            options[no_match] = None
        if any(not isinstance(k, str) or not k.strip() for k in options):
            raise QuestionError("option names must be non-empty strings")
        if not 2 <= len(options) <= MAX_OPTIONS:
            raise QuestionError(f"a choice takes 2-{MAX_OPTIONS} options, got {len(options)}")
        self.options, self.no_match = options, no_match

    def wire(self):
        return {"type": "choice", "instructions": self.instructions,
                "criteria": dict(self.options)}

    def parse(self, raw, model=None, source="jev"):
        raw = self._check_type(raw)
        choice = raw.get("choice")
        if choice not in self.options:
            raise AnswerError(f"choice {choice!r} is not one of {sorted(self.options)}")
        probs = {str(k): _num(v) for k, v in (raw.get("probabilities") or {}).items()
                 if k in self.options and _num(v) is not None}
        conf = _num(raw.get("confidence"))
        p = probs.get(choice, conf)
        return Answer("choice", choice, p=p, probabilities=probs or None, confidence=conf,
                      source=source, model=model)


class Score(Question):
    kind = "score"

    def __init__(self, version: str, instructions: Any, levels: Sequence[str]):
        super().__init__(version, instructions)
        levels = list(levels)
        if not MIN_LEVELS <= len(levels) <= MAX_LEVELS:
            raise QuestionError(
                f"a score takes {MIN_LEVELS}-{MAX_LEVELS} levels, got {len(levels)}")
        if any(not isinstance(l, str) or not l.strip() for l in levels):
            raise QuestionError("score levels must be non-empty strings")
        self.levels = levels

    def wire(self):
        return {"type": "score", "instructions": self.instructions, "criteria": list(self.levels)}

    def parse(self, raw, model=None, source="jev"):
        raw = self._check_type(raw)
        value = _num(raw.get("score"))
        if value is None or not 0.0 <= value <= len(self.levels) - 1:
            raise AnswerError(f"score out of range: {raw.get('score')!r}")
        probs = {str(k): _num(v) for k, v in (raw.get("probabilities") or {}).items()
                 if _num(v) is not None}
        conf = _num(raw.get("confidence"))
        return Answer("score", value, p=conf, probabilities=probs or None, confidence=conf,
                      source=source, model=model)


def estimate_tokens(text: str) -> int:
    """A deliberately high token estimate (3 characters per token) for the
    documented 64k / 32k request limits; the API counts, this only pre-checks."""
    return len(text) // 3 + 1
