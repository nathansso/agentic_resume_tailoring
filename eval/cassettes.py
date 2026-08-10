"""Record/replay of LLM responses for the benchmark harness (issue #171).

Product-mode fidelity costs real API spend, which is why the benchmark grew a
stub in the first place — and why every number it reported described the harness
rather than the product. Record/replay is the mechanism that makes production
fidelity affordable to repeat: snapshot each request/response once, replay
thereafter, with every deterministic line of the pipeline still executing for
real.

**The seam is `llm.get_llm`.** Every model call in the app resolves through it
(directly, or via `llm.get_extractor`, which looks the factory up in module
globals at call time), so a single patch covers both surfaces the app uses:

* ``llm.invoke(prompt_value)`` → ``AIMessage`` — the tailor/chat
  ``prompt | llm | JsonOutputParser`` chains.
* ``llm.with_structured_output(schema)`` → a validated Pydantic model — the
  #142 extraction seam, which ``agents/jd_profile.py`` reaches without passing
  an ``llm`` at all.

**The key is ``(scope, role, sha256(rendered_prompt), occurrence)``.** The
prompt hash alone is not sufficient: ``agents/tailor.py`` runs at
``temperature=0.3`` with ``MAX_RETRIES = 2``, so the attempt count is
data-dependent and nothing structurally prevents one rendered prompt being
invoked twice and legitimately returning two different samples. The occurrence
counter costs nothing and removes that class of bug rather than relying on a
property that merely happens to hold today. ``scope`` is the benchmark task id
(or ``SETUP_SCOPE`` for the one-time register/ingest phase), so each task's
counters are independent and a task is replayable without replaying its
predecessors' calls.

Occurrence numbers are assigned **on success**, never on dispatch: a failed
provider call or a validation retry records nothing and advances nothing, so the
replayed stream is the stream of responses that actually came back.

**A cassette miss in replay mode is fatal and never falls through to a live
call.** Several call sites (`agents/parser.py`, `agents/jd_profile.py`) catch
every exception and degrade to an empty result, which would turn a miss into a
silently empty parse — so misses are also accumulated on the session and the
harness fails the run on any non-zero count.

A prompt change invalidating its cassette is **correct behaviour**, not a
failure: it makes every prompt edit an explicitly re-measured event.
"""
import hashlib
import json
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

CASSETTE_VERSION = 1
CASSETTE_DIR = Path(__file__).resolve().parent / "cassettes"

# Scope for LLM calls made outside any task: registration and résumé ingestion.
SETUP_SCOPE = "__setup__"

MODE_RECORD = "record"
MODE_REPLAY = "replay"

SURFACE_INVOKE = "invoke"
SURFACE_STRUCTURED = "structured"

PREVIEW_CHARS = 240


class CassetteMiss(RuntimeError):
    """Replay was asked for an interaction the recording does not contain."""


def render_prompt(prompt_value) -> str:
    """Text of a PromptValue, a list of formatted messages, or a raw string."""
    if hasattr(prompt_value, "to_string"):
        return prompt_value.to_string()
    if isinstance(prompt_value, (list, tuple)):
        return "\n".join(str(getattr(m, "content", m)) for m in prompt_value)
    return str(prompt_value)


def prompt_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def interaction_key(scope: str, role: str, sha: str, occurrence: int) -> str:
    return f"{scope}|{role}|{sha}|{occurrence}"


class Cassette:
    """One run's recorded interactions, on disk as reviewable JSON."""

    def __init__(self, interactions: Optional[List[Dict]] = None,
                 meta: Optional[Dict] = None):
        self.meta = dict(meta or {})
        self._by_key: Dict[str, Dict] = {}
        self._order: List[Dict] = []
        for entry in interactions or []:
            self.put(entry)

    # ── persistence ───────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path) -> "Cassette":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"no cassette at {path} — record one first with "
                f"`--mode product --record-cassette {path}`"
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        version = raw.get("version")
        if version != CASSETTE_VERSION:
            raise ValueError(
                f"{path}: cassette version {version!r}, expected {CASSETTE_VERSION}"
            )
        meta = {k: v for k, v in raw.items() if k != "interactions"}
        return cls(raw.get("interactions", []), meta)

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "version": CASSETTE_VERSION,
            **self.meta,
            "interaction_count": len(self._order),
            "interactions": self._order,
        }
        path.write_text(json.dumps(body, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        return path

    # ── contents ──────────────────────────────────────────────────────────

    def put(self, entry: Dict) -> None:
        key = interaction_key(entry["scope"], entry["role"],
                              entry["prompt_sha256"], entry["occurrence"])
        if key in self._by_key:
            raise ValueError(f"duplicate cassette key: {key}")
        self._by_key[key] = entry
        self._order.append(entry)

    def get(self, key: str) -> Optional[Dict]:
        return self._by_key.get(key)

    def scopes(self) -> List[str]:
        seen = []
        for entry in self._order:
            if entry["scope"] not in seen:
                seen.append(entry["scope"])
        return seen

    def __len__(self) -> int:
        return len(self._order)


class CassetteSession:
    """Binds a cassette to one benchmark run and hands out the `get_llm` seam.

    ``factory`` is the real ``llm.get_llm``; it is only ever called in record
    mode, so a replay run cannot reach a provider even if API keys are present.
    """

    def __init__(self, cassette: Cassette, mode: str, factory=None):
        if mode not in (MODE_RECORD, MODE_REPLAY):
            raise ValueError(f"unknown cassette mode: {mode!r}")
        if mode == MODE_RECORD and factory is None:
            raise ValueError("record mode needs the real get_llm factory")
        self.cassette = cassette
        self.mode = mode
        self._factory = factory
        self._scope = SETUP_SCOPE
        self._counters: Dict[str, int] = {}
        self.misses: List[Dict] = []

    # ── scope ─────────────────────────────────────────────────────────────

    @contextmanager
    def scope(self, name: str):
        """Scope every interaction inside the block to `name` (a task id).

        Counters are per scope, so replaying one task does not depend on the
        call counts of the tasks recorded before it.
        """
        previous = self._scope
        self._scope = name
        try:
            yield self
        finally:
            self._scope = previous

    @property
    def current_scope(self) -> str:
        return self._scope

    # ── the seam ──────────────────────────────────────────────────────────

    def llm_factory(self):
        """A drop-in for `llm.get_llm`."""
        def _factory(role: str = "chat", temperature: float = 0.0):
            return _CassetteLLM(self, role, temperature)
        return _factory

    # ── record / replay ───────────────────────────────────────────────────

    def _next_occurrence(self, role: str, sha: str) -> int:
        counter_key = f"{self._scope}|{role}|{sha}"
        occurrence = self._counters.get(counter_key, 0)
        self._counters[counter_key] = occurrence + 1
        return occurrence

    def record(self, role: str, prompt_text: str, surface: str, response,
               *, schema: Optional[str] = None, temperature: float = 0.0) -> None:
        sha = prompt_sha256(prompt_text)
        self.cassette.put({
            "scope": self._scope,
            "role": role,
            "prompt_sha256": sha,
            "occurrence": self._next_occurrence(role, sha),
            "surface": surface,
            "schema": schema,
            "temperature": temperature,
            "prompt_preview": prompt_text[:PREVIEW_CHARS],
            "response": response,
        })

    def replay(self, role: str, prompt_text: str, surface: str,
               *, schema: Optional[str] = None):
        sha = prompt_sha256(prompt_text)
        occurrence = self._counters.get(f"{self._scope}|{role}|{sha}", 0)
        key = interaction_key(self._scope, role, sha, occurrence)
        entry = self.cassette.get(key)
        if entry is None:
            self._miss(key, role, prompt_text, surface, schema,
                       "no recorded interaction for this key")
        if entry["surface"] != surface:
            self._miss(key, role, prompt_text, surface, schema,
                       f"recorded as {entry['surface']!r}, replayed as {surface!r}")
        if schema is not None and entry.get("schema") != schema:
            self._miss(key, role, prompt_text, surface, schema,
                       f"recorded schema {entry.get('schema')!r}, replayed {schema!r}")
        # Only a served hit advances the counter, mirroring record-on-success.
        self._next_occurrence(role, sha)
        return entry["response"]

    def _miss(self, key, role, prompt_text, surface, schema, reason) -> None:
        miss = {
            "key": key,
            "scope": self._scope,
            "role": role,
            "surface": surface,
            "schema": schema,
            "reason": reason,
            "prompt_preview": prompt_text[:PREVIEW_CHARS],
        }
        self.misses.append(miss)
        # Printed as it happens because several call sites catch every
        # exception and degrade to an empty result — without this, a miss can
        # reach the metrics as a silently empty parse rather than as an error.
        print(f"\nCASSETTE MISS ({reason})\n  key: {key}\n"
              f"  prompt: {prompt_text[:PREVIEW_CHARS]!r}\n"
              "  A prompt change invalidates its cassette — re-record it.",
              file=sys.stderr, flush=True)
        raise CassetteMiss(f"{reason}: {key}")

    def assert_no_misses(self) -> None:
        """Fail the run if any interaction was missing from the recording."""
        if not self.misses:
            return
        lines = "\n".join(f"  - {m['scope']} / {m['role']} / {m['reason']}"
                          for m in self.misses)
        raise SystemExit(
            f"replay hit {len(self.misses)} cassette miss(es); the recording "
            f"does not cover this run:\n{lines}\n"
            "Re-record with --mode product --record-cassette <path>."
        )


class _CassetteLLM:
    """The object `get_llm` returns under record/replay.

    Not a `langchain_core.runnables.Runnable` subclass on purpose: the app only
    ever uses `.invoke` and `.with_structured_output` on the result of
    `get_llm`, and staying a plain object keeps the recorded surface exactly the
    surface the app exercises — anything else fails loudly here rather than
    quietly reaching a provider in replay.
    """

    def __init__(self, session: CassetteSession, role: str, temperature: float):
        self._session = session
        self._role = role
        self._temperature = temperature
        self._real = None

    def _real_llm(self):
        if self._real is None:
            self._real = self._session._factory(self._role,
                                                temperature=self._temperature)
        return self._real

    def invoke(self, input, config=None, **kwargs):
        from langchain_core.messages import AIMessage

        text = render_prompt(input)
        if self._session.mode == MODE_REPLAY:
            return AIMessage(content=self._session.replay(
                self._role, text, SURFACE_INVOKE))
        message = self._real_llm().invoke(input, config=config, **kwargs)
        self._session.record(self._role, text, SURFACE_INVOKE, message.content,
                             temperature=self._temperature)
        return message

    def with_structured_output(self, schema, **kwargs):
        from langchain_core.runnables import RunnableLambda

        session, role, temperature = self._session, self._role, self._temperature
        schema_name = getattr(schema, "__name__", str(schema))

        def _call(prompt_value):
            text = render_prompt(prompt_value)
            if session.mode == MODE_REPLAY:
                payload = session.replay(role, text, SURFACE_STRUCTURED,
                                         schema=schema_name)
                return schema(**payload)
            result = self._real_llm().with_structured_output(
                schema, **kwargs).invoke(prompt_value)
            if result is None:
                # Let llm.StructuredExtractor's retry see this exactly as it
                # would live: nothing recorded, no occurrence consumed.
                raise ValueError("structured extractor returned no result")
            session.record(role, text, SURFACE_STRUCTURED,
                           _dump(result), schema=schema_name,
                           temperature=temperature)
            return result

        return RunnableLambda(_call)


def _dump(model) -> Dict:
    """Pydantic model → JSON-safe dict for the cassette."""
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    if hasattr(model, "dict"):  # pydantic v1 fallback
        return model.dict()
    raise TypeError(f"cannot serialize structured output of type {type(model)!r}")


def default_cassette_path(profile_name: str, limit: int, tasks) -> Path:
    """Stable, self-describing default name for a recording."""
    if tasks:
        scope = "tasks-" + "_".join(sorted(tasks))[:60]
    elif limit:
        scope = f"limit{limit}"
    else:
        scope = "all"
    return CASSETTE_DIR / f"{profile_name}_{scope}.json"


def recording_meta(profile: str, tasks: List[str]) -> Dict:
    import os

    from config import EXTRACT_MODEL, TAILOR_MODEL, normalize_provider

    return {
        "recorded_at": datetime.now().strftime("%Y%m%dT%H%M%S"),
        "provider": normalize_provider(os.environ.get("LLM_PROVIDER")) or "",
        "models": {"extract": EXTRACT_MODEL, "tailor": TAILOR_MODEL},
        "profile": profile,
        "tasks": tasks,
    }
