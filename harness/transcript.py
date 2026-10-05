"""The previous assistant turn, read from the host's transcript (issue #244).

The memory gate (`harness/decisions/memory_gate.py`, `memory_gate@v2`) reads a message such as "never list
that again" against the assistant turn it answers. Both hosts pass their hooks a `transcript_path` to a JSONL
file; this module reads the last assistant text out of the end of it.

- **Claude Code** (`UserPromptSubmit` input has `transcript_path`): one JSON object per line,
  `{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": ...},
  {"type": "tool_use", ...}]}}`; a user line carries `content` as a string or a list whose blocks are `text`
  (the user) or `tool_result` (a tool answering). The file is written asynchronously, so the current prompt is
  usually not in it yet when the hook fires; a trailing copy of it is skipped.
- **Codex** (the same hook input; `transcript_path` is nullable): a rollout file, one
  `{"type": "response_item", "payload": {"type": "message", "role": "assistant", "content":
  [{"type": "output_text", "text": ...}]}}` per line, plus `event_msg` lines. The docs say the transcript format is
  not a stable interface, so this is read defensively: a line that does not parse is skipped, and no
  readable assistant turn means v1.

**Bounded and silent.** The file is read backwards in blocks, at most `MAX_BYTES` of it and for at most
`MAX_SECONDS`; only text is kept (tool calls and tool results are dropped); the turn is cut to
`memory_gate.PREVIOUS_MAX` characters. Every failure (no path, a missing or unreadable file, bad JSON, no
assistant turn, a line too large to read) returns None and the gate runs v1. Nothing here raises.

Model-free by rule (`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

log = logging.getLogger(__name__)

MAX_BYTES = 1_000_000        # how much of the end of the file is ever read
MAX_SECONDS = 0.5            # and for how long
BLOCK = 65_536
MAX_LINES = 400              # lines looked at before giving up


def _lines_from_end(fh, size: int, max_bytes: int) -> Iterator[bytes]:
    """The file's lines, last first, reading blocks backwards, at most `max_bytes` in all. A line cut by
    the byte limit is not yielded."""
    pos, read, tail = size, 0, b""
    while pos > 0 and read < max_bytes:
        step = min(BLOCK, pos, max_bytes - read)
        pos -= step
        read += step
        fh.seek(pos)
        data = fh.read(step) + tail
        parts = data.split(b"\n")
        tail = parts[0]
        for line in reversed(parts[1:]):
            yield line
    if pos == 0 and tail:
        yield tail                                  # the first line of the file, whole


def _text_of(content: Any) -> str:
    """The text blocks of a message's `content`, joined; tool calls, tool results and images dropped."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: List[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in ("text", "output_text", "input_text") \
                and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(p for p in parts if p.strip())


def _entry(line: bytes) -> Optional[Dict[str, Any]]:
    """One transcript line as `{role, text, tool}` (`tool` when it is a tool result or call with no
    text), or None for a line that is neither a user nor an assistant message."""
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    if not isinstance(obj, dict) or obj.get("isSidechain"):
        return None
    kind = obj.get("type")
    if kind in ("user", "assistant") and isinstance(obj.get("message"), dict):     # Claude Code
        msg = obj["message"]
        role = msg.get("role") or kind
        content = msg.get("content")
    elif kind == "response_item" and isinstance(obj.get("payload"), dict) \
            and obj["payload"].get("type") == "message":                            # Codex rollout
        role = obj["payload"].get("role")
        content = obj["payload"].get("content")
    else:
        return None
    if role not in ("user", "assistant"):
        return None
    tool = isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") in ("tool_result", "tool_use") for b in content)
    return {"role": role, "text": _text_of(content).strip(), "tool": tool, "meta": bool(obj.get("isMeta"))}


def previous_assistant_turn(path: Optional[str], prompt: Optional[str] = None, *,
                            max_bytes: Optional[int] = None, max_seconds: Optional[float] = None) -> Optional[str]:
    """The text of the last assistant message before the current prompt, or None.

    Reads from the end of the file. `prompt` is the current message: a trailing user line that repeats it
    is the prompt itself (the host had already written it) and is skipped. The scan stops at the previous
    genuine user message, so a turn the assistant answered with tool calls alone gives None rather than an
    older turn's text. The result is not truncated here (`memory_gate.clip_previous` does that)."""
    if not path or not isinstance(path, str):
        return None
    try:
        p = Path(path)
        size = p.stat().st_size
        if size <= 0:
            return None
        max_bytes = MAX_BYTES if max_bytes is None else max_bytes
        deadline = time.monotonic() + (MAX_SECONDS if max_seconds is None else max_seconds)
        current = " ".join((prompt or "").split())
        seen_other = False                          # an assistant or tool line has been passed
        with p.open("rb") as fh:
            for n, line in enumerate(_lines_from_end(fh, size, max_bytes)):
                if n >= MAX_LINES or time.monotonic() > deadline:
                    return None
                if not line.strip():
                    continue
                e = _entry(line)
                if e is None:
                    continue
                if e["role"] == "assistant":
                    seen_other = True
                    if e["text"]:
                        return e["text"]
                    continue
                if e["tool"] or e["meta"]:          # a tool result, or a system-injected user line
                    seen_other = True
                    continue
                if not seen_other and current and " ".join(e["text"].split()) == current:
                    continue                        # the current prompt, already written
                return None                         # the previous user message: no assistant text since
    except Exception as exc:                        # never break the prompt
        log.debug("transcript: could not read %r: %s", path, exc)
    return None
