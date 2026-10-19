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




# ── the requests (one shape for both backends, so the cache key is the same) ──

def write_params(repo_name: str, user_label: str, dossier: str, sample: int = 0) -> dict:
    return {
        "model": WRITER_MODEL, "max_tokens": 16000,
        "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": WRITER_SCHEMA}},
        "system": WRITER_SYSTEM,
        "messages": [{"role": "user", "content":
                      f"Repository: {repo_name}\nThe user: {user_label}\n\n{dossier}"}],
        "metadata": {"user_id": f"eval397-s{sample}"},
    }


def judge_params(repo_name: str, user_label: str, evidence: str, output: dict) -> dict:
    system = [
        {"type": "text", "text": JUDGE_INSTRUCTIONS.format(user=user_label)},
        {"type": "text", "text": f"<repository name=\"{repo_name}\">\n{evidence}\n</repository>",
         "cache_control": {"type": "ephemeral"}},
    ]
    body = "Description: " + output["description"] + "\n" + "\n".join(
        f"Bullet {i + 1}: {b}" for i, b in enumerate(output["bullets"]))
    return {
        "model": JUDGE_MODEL, "max_tokens": 32000,
        "output_config": {"effort": "high", "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
        "system": system,
        "messages": [{"role": "user", "content": body}],
    }


def coverage_params(facts: list[str], output: dict) -> dict:
    listing = "\n".join(f"{i}. {f}" for i, f in enumerate(facts))
    body = ("Reference facts:\n" + listing + "\n\nContent:\nDescription: " + output["description"]
            + "\n" + "\n".join(f"Bullet {i + 1}: {b}" for i, b in enumerate(output["bullets"])))
    return {
        "model": JUDGE_MODEL, "max_tokens": 8000,
        "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": COVERAGE_SCHEMA}},
        "system": COVERAGE_SYSTEM,
        "messages": [{"role": "user", "content": body}],
    }


class _Cached:
    """The on-disk response cache both backends share: a request's hash names its file."""

    def __init__(self, cache_dir: Path, ledger: Ledger):
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger

    def _send(self, params: dict) -> tuple[dict, dict, str]:
        raise NotImplementedError

    def _call(self, tag: str, params: dict) -> tuple[dict, dict, bool]:
        """(parsed JSON, usage, cached?). Cached responses add nothing to the ledger."""
        digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:24]
        path = self.cache_dir / f"{tag}-{digest}.json"
        if path.exists():
            rec = json.loads(path.read_text(encoding="utf-8"))
            return rec["parsed"], rec["usage"], True
        self.ledger.check()
        parsed, usage, stop = self._send(params)
        self.ledger.add(params["model"], usage)
        path.write_text(json.dumps({"tag": tag, "parsed": parsed, "usage": usage, "stop_reason": stop,
                                    "backend": self.backend}, indent=1), encoding="utf-8")
        return parsed, usage, False

    def write(self, repo_name: str, user_label: str, dossier: str, sample: int = 0):
        return self._call("write", write_params(repo_name, user_label, dossier, sample))

    def judge(self, repo_name: str, user_label: str, evidence: str, output: dict):
        return self._call("judge", judge_params(repo_name, user_label, evidence, output))

    def coverage(self, facts: list[str], output: dict):
        return self._call("coverage", coverage_params(facts, output))


class Client(_Cached):
    """The Anthropic API (billed to the key in `.env`). #397's spike used this."""
    backend = "api"

    def __init__(self, cache_dir: Path, ledger: Ledger, env_file: Path | None = None):
        import anthropic
        super().__init__(cache_dir, ledger)
        key = load_api_key(env_file)
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set (environment or .env)")
        self._api = anthropic.Anthropic(api_key=key, max_retries=4)

    def _send(self, params):
        with self._api.messages.stream(**params) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            raise RuntimeError("refused")
        text = "".join(b.text for b in msg.content if b.type == "text")
        u = msg.usage
        usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                 "cache_creation_input_tokens": u.cache_creation_input_tokens or 0,
                 "cache_read_input_tokens": u.cache_read_input_tokens or 0}
        return json.loads(text), usage, msg.stop_reason


class CliClient(_Cached):
    """Claude Code's print mode (`claude -p`) on the signed-in subscription: no API spend.

    The request is the API one, mapped onto the CLI: the system blocks become one system-prompt
    file (so the judge's repository evidence is a stable, cacheable prefix), the user message
    goes on stdin, the schema to `--json-schema`, the effort to `--effort`. Tools, MCP servers,
    settings (and so hooks) and session persistence are off, and it runs from an empty folder
    outside any repository so no project CLAUDE.md is read (refused otherwise). Any ANTHROPIC_API_KEY is removed from the child's environment, so
    the CLI cannot fall back to API billing. `total_cost_usd` is recorded as the list-price
    equivalent, not a charge.
    """
    backend = "claude-cli"

    def __init__(self, cache_dir: Path, ledger: Ledger, workdir: Path, exe: str | None = None,
                 timeout_s: int = 1800):
        import shutil
        super().__init__(cache_dir, ledger)
        self.exe = exe or shutil.which("claude")
        if not self.exe:
            raise RuntimeError("the claude CLI is not on PATH")
        # Claude Code reads every CLAUDE.md from the working folder up: inside this repo that
        # would hand the writer the repo's guidance and the owner's private personal/ notes
        for d in [workdir.resolve(), *workdir.resolve().parents]:
            if (d / "CLAUDE.md").exists() or (d / ".git").exists():
                raise RuntimeError(f"the CLI working folder {workdir} is under {d}, which has a "
                                   "CLAUDE.md or is a repository; use a folder outside it")
        self.workdir = workdir
        workdir.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s

    def _send(self, params):
        import subprocess
        import tempfile
        system = params["system"]
        if isinstance(system, list):
            system = "\n\n".join(b["text"] for b in system)
        fmt = params["output_config"]["format"]["schema"]
        env = {k: v for k, v in os.environ.items()
               if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
                            "CLAUDE_CODE_USE_VERTEX")}
        with tempfile.NamedTemporaryFile("w", suffix=".txt", dir=self.workdir, delete=False,
                                         encoding="utf-8") as fh:
            fh.write(system)
            sys_path = fh.name
        try:
            done = subprocess.run(
                [self.exe, "-p", "--model", params["model"], "--effort", params["output_config"]["effort"],
                 "--output-format", "json", "--json-schema", json.dumps(fmt),
                 "--system-prompt-file", sys_path, "--tools", "", "--no-session-persistence",
                 "--strict-mcp-config", "--setting-sources", ""],
                input=params["messages"][0]["content"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", cwd=self.workdir, env=env, timeout=self.timeout_s)
        finally:
            os.unlink(sys_path)
        try:
            out = json.loads(done.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"claude -p failed ({done.returncode}): {(done.stderr or done.stdout)[-400:]}")
        if out.get("is_error") or out.get("subtype") != "success" or out.get("structured_output") is None:
            raise RuntimeError(f"claude -p: {out.get('subtype')} {out.get('api_error_status')} "
                               f"{str(out.get('result'))[:300]}")
        u = out.get("usage", {})
        usage = {"input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0),
                 "cache_creation_input_tokens": u.get("cache_creation_input_tokens", 0),
                 "cache_read_input_tokens": u.get("cache_read_input_tokens", 0),
                 "list_usd": out.get("total_cost_usd", 0.0), "duration_ms": out.get("duration_ms")}
        return out["structured_output"], usage, out.get("stop_reason")
