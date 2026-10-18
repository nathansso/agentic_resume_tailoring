"""Writer, judge and coverage calls for the repo-map sufficiency spike (issue #397).

The writer (one model for every rung) turns a dossier into a project description and three
resume bullets. The judge (a stronger model) reads the repository — README, metadata, the full
code to a large cap and every author's git history — splits each output into atomic claims and
labels each supported, unsupported or contradicted. For the owner's repos a separate call scores
coverage: which facts from the approved bullets an output recovers. That call sees no code, so
coverage and accuracy are judged apart.

Every response is cached on disk by a hash of its request, so a rerun spends nothing for work
already done. The API key comes from the environment or the repo's `.env` and is never printed.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

WRITER_MODEL = "claude-sonnet-5-5"
JUDGE_MODEL = "claude-opus-5-5"
PRICES = {  # $ per million tokens: input, output, cache write (5 min), cache read
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
}

WRITER_SYSTEM = """You write resume content for the projects section of a software engineer's resume.

You get material about one GitHub repository and, when the material includes it, the user's part in it. Write:
- "description": what the project is, in one or two sentences.
- "bullets": exactly three resume bullets about what the user built or did in this project.

Be specific: name the techniques, tools, scale and results the material shows. Every statement must be supported by the material; do not invent numbers, outcomes, users or features. Where the material does not show the user's own part, describe the project's work without claiming more than it shows."""

WRITER_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "bullets": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["description", "bullets"],
    "additionalProperties": False,
}

JUDGE_INSTRUCTIONS = """You audit resume content written about a GitHub repository. Below is the repository as evidence: its metadata, file tree, every author's git history, and its files (README, code, notebooks with their outputs; data files shown by their first lines).

You will get a project description and three bullets written for {user}. Split them into atomic factual claims: one checkable fact each (a technique, a tool, a number, a result, a feature, a scale, the user's role in a piece of work). Fold vague or subjective wording into the claim it modifies rather than making it a claim. Then label each claim against the evidence:
- "supported": the files or the history show it.
- "unsupported": nothing in the evidence shows it (an invented number, outcome, user count or feature; a role the history does not show).
- "contradicted": the evidence shows otherwise (a wrong library, a wrong number, a feature that works differently, or work the history shows another author did attributed to the user).

Set "about_user_role" true when the claim says the user personally did a specific piece of work. Give "evidence" as a file path, commit or short quote, or what you looked for and did not find. Judge only against the evidence below, not against what is plausible."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "part": {"type": "string", "enum": ["description", "bullet 1", "bullet 2", "bullet 3"]},
                    "claim": {"type": "string"},
                    "label": {"type": "string", "enum": ["supported", "unsupported", "contradicted"]},
                    "about_user_role": {"type": "boolean"},
                    "evidence": {"type": "string"},
                },
                "required": ["part", "claim", "label", "about_user_role", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["claims"],
    "additionalProperties": False,
}

COVERAGE_SYSTEM = """You compare resume content against a list of reference facts about the same project. For each reference fact, say whether the content states it: "full" (the fact is stated, in any wording, with its key specifics such as the number or the named technique), "partial" (the gist is there but a key specific is missing or vaguer), or "none". A fact only counts if the content states it; do not credit facts the content merely makes plausible."""

COVERAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "recovered": {"type": "string", "enum": ["full", "partial", "none"]},
                },
                "required": ["id", "recovered"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["facts"],
    "additionalProperties": False,
}


def load_api_key(env_file: Path | None) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key or env_file is None or not env_file.exists():
        return key
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("ANTHROPIC_API_KEY"):
            _, _, value = line.partition("=")
            return value.strip().strip("\"'")
    return ""


class Ledger:
    """Token and dollar totals across calls, with a hard stop."""

    def __init__(self, limit_usd: float):
        self.limit = limit_usd
        self.usd = 0.0
        self.calls = 0
        self.by_model: dict[str, dict] = {}
        self._lock = threading.Lock()

    @staticmethod
    def cost(model: str, usage: dict) -> float:
        i, o, cw, cr = PRICES[model]
        return (usage.get("input_tokens", 0) * i + usage.get("output_tokens", 0) * o
                + usage.get("cache_creation_input_tokens", 0) * cw
                + usage.get("cache_read_input_tokens", 0) * cr) / 1e6

    def add(self, model: str, usage: dict) -> None:
        with self._lock:
            c = self.cost(model, usage)
            self.usd += c
            self.calls += 1
            m = self.by_model.setdefault(model, {"calls": 0, "usd": 0.0, "input_tokens": 0,
                                                 "output_tokens": 0, "cache_read": 0, "cache_write": 0})
            m["calls"] += 1
            m["usd"] += c
            m["input_tokens"] += usage.get("input_tokens", 0)
            m["output_tokens"] += usage.get("output_tokens", 0)
            m["cache_read"] += usage.get("cache_read_input_tokens", 0)
            m["cache_write"] += usage.get("cache_creation_input_tokens", 0)

    def check(self) -> None:
        if self.usd > self.limit:
            raise RuntimeError(f"spend ${self.usd:.2f} passed the ${self.limit:.2f} limit; stopping")


class Client:
    def __init__(self, cache_dir: Path, ledger: Ledger, env_file: Path | None = None):
        import anthropic
        key = load_api_key(env_file)
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set (environment or .env)")
        self._api = anthropic.Anthropic(api_key=key, max_retries=4)
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger

    def _call(self, tag: str, params: dict) -> tuple[dict, dict, bool]:
        """(parsed JSON, usage, cached?). Cached responses add nothing to the ledger."""
        digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:24]
        path = self.cache_dir / f"{tag}-{digest}.json"
        if path.exists():
            rec = json.loads(path.read_text(encoding="utf-8"))
            return rec["parsed"], rec["usage"], True
        self.ledger.check()
        with self._api.messages.stream(**params) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            raise RuntimeError(f"{tag}: refused")
        text = "".join(b.text for b in msg.content if b.type == "text")
        parsed = json.loads(text)
        u = msg.usage
        usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                 "cache_creation_input_tokens": u.cache_creation_input_tokens or 0,
                 "cache_read_input_tokens": u.cache_read_input_tokens or 0}
        self.ledger.add(params["model"], usage)
        path.write_text(json.dumps({"tag": tag, "parsed": parsed, "usage": usage,
                                    "stop_reason": msg.stop_reason}, indent=1), encoding="utf-8")
        return parsed, usage, False

    def write(self, repo_name: str, user_label: str, dossier: str, sample: int = 0):
        params = {
            "model": WRITER_MODEL, "max_tokens": 16000,
            "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": WRITER_SCHEMA}},
            "system": WRITER_SYSTEM,
            "messages": [{"role": "user", "content":
                          f"Repository: {repo_name}\nThe user: {user_label}\n\n{dossier}"}],
            "metadata": {"user_id": f"eval397-s{sample}"},
        }
        return self._call("write", params)

    def judge(self, repo_name: str, user_label: str, evidence: str, output: dict):
        system = [
            {"type": "text", "text": JUDGE_INSTRUCTIONS.format(user=user_label)},
            {"type": "text", "text": f"<repository name=\"{repo_name}\">\n{evidence}\n</repository>",
             "cache_control": {"type": "ephemeral"}},
        ]
        body = "Description: " + output["description"] + "\n" + "\n".join(
            f"Bullet {i + 1}: {b}" for i, b in enumerate(output["bullets"]))
        params = {
            "model": JUDGE_MODEL, "max_tokens": 32000,
            "output_config": {"effort": "high", "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
            "system": system,
            "messages": [{"role": "user", "content": body}],
        }
        return self._call("judge", params)

    def coverage(self, facts: list[str], output: dict):
        listing = "\n".join(f"{i}. {f}" for i, f in enumerate(facts))
        body = ("Reference facts:\n" + listing + "\n\nContent:\nDescription: " + output["description"]
                + "\n" + "\n".join(f"Bullet {i + 1}: {b}" for i, b in enumerate(output["bullets"])))
        params = {
            "model": JUDGE_MODEL, "max_tokens": 8000,
            "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": COVERAGE_SCHEMA}},
            "system": COVERAGE_SYSTEM,
            "messages": [{"role": "user", "content": body}],
        }
        return self._call("coverage", params)
