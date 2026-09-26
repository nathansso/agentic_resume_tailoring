"""Block render cache: rendered lines per bullet, measured once (issue #200).

The per-bullet two-line gate and the page line budget (`agents.checks`) need
to know how many lines each bullet renders to. Only TeX knows, so this module
asks it once per distinct (text, template) and stores the answer in
`BlockLineCache`:

- `measure_lines(texts)` compiles **one** document holding every text as a
  bullet, with the formatter's own preamble, list nesting and escaping, and
  has TeX report each item paragraph's line count (`\\prevgraf`) to the log.
- `bullet_lines(texts)` serves cache hits from the table without compiling and
  batches every miss into a single `measure_lines` call.

**Model-free.** Nothing here may import a generative client
(`tests/test_harness_boundary.py`).

Measurement detail, verified against real compiles: `\\prevgraf` must be copied
into a count register *before* `\\typeout`. TeX zeroes `\\prevgraf` while it
expands a `\\write` (tex.web §1370 sets `mode:=0` "to disable \\prevgraf"), so
`\\typeout{\\the\\prevgraf}` always logs 0.
"""
from __future__ import annotations

import functools
import hashlib
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence

from sqlmodel import Session, select

import database.db as db
from agents.checks import PAGE_LINE_BUDGET, bullet_texts, page_line_budget
from agents.formatter import (
    ResumeFormatterAgent,
    _JAKE_PREAMBLE,
    _convert_inline,
    _find_latex_engine,
)
from agents.skill_postprocessor import should_reject_skill
from database.models import BlockLineCache

logger = logging.getLogger(__name__)

# The nesting `_build_tex_experiences` / `_build_tex_projects` put around every
# bullet: an outer `\resumeSubHeadingListStart` item, then the inner
# `\resumeItemListStart` list. Line width depends only on this nesting, so one
# measurement serves experience, project and achievement bullets alike.
_PROBE_SETUP = r"\newcount\artlines"
_OPEN = "\n".join([
    r"\begin{document}",
    r"  \resumeSubHeadingListStart",
    r"    \resumeSubheading{x}{x}{x}{x}",
    r"      \resumeItemListStart",
])
_ITEM = (r"        \resumeItem{%s}"
         r"\par\global\artlines=\prevgraf\typeout{ARTLINES:%d:\the\artlines}")
_CLOSE = "\n".join([
    r"      \resumeItemListEnd",
    r"  \resumeSubHeadingListEnd",
    r"\end{document}",
])
_ARTLINES = re.compile(r"ARTLINES:(\d+):(\d+)")


def render_bullet_tex(text: str) -> str:
    """A bullet's LaTeX exactly as the formatter emits it ('' if blank)."""
    return _convert_inline(text.strip()) if text and text.strip() else ""


def _text_hash(tex: str) -> str:
    return hashlib.sha256(tex.encode("utf-8")).hexdigest()


@functools.lru_cache(maxsize=None)
def engine_id() -> str:
    """Engine name and version, e.g. 'Tectonic 0.16.9'; 'none' without one."""
    found = _find_latex_engine()
    if not found:
        return "none"
    name, path = found
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True,
                             timeout=30).stdout.strip().splitlines()
    except Exception:
        return name
    version = out[0].strip() if out else ""
    return version if name in version.lower() else f"{name} {version}".strip()


def template_hash(preamble: str = _JAKE_PREAMBLE, engine: Optional[str] = None) -> str:
    """sha256 over everything but the text that decides a bullet's line breaks."""
    parts = [preamble, _PROBE_SETUP, _OPEN, _ITEM, _CLOSE,
             engine if engine is not None else engine_id()]
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def measurement_tex(texts: Sequence[str]) -> str:
    """The one document `measure_lines` compiles for `texts`."""
    items = [_ITEM % (render_bullet_tex(t), i) for i, t in enumerate(texts)]
    return "\n".join([_JAKE_PREAMBLE, _PROBE_SETUP, _OPEN, *items, _CLOSE])


def measure_lines(texts: Sequence[str]) -> List[int]:
    """Rendered line count of each text as a resume bullet, in one compile.

    Raises RuntimeError when no LaTeX engine is installed or the compile does
    not report every bullet.
    """
    texts = list(texts)
    if not texts:
        return []
    if any(not render_bullet_tex(t) for t in texts):
        raise ValueError("measure_lines: blank text (blank bullets never render)")
    found = _find_latex_engine()
    if not found:
        raise RuntimeError("No LaTeX engine found. Install tectonic or pdflatex.")
    name, path = found
    with tempfile.TemporaryDirectory() as tmp:
        tex_path = os.path.join(tmp, "measure.tex")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(measurement_tex(texts))
        if name == "tectonic":
            cmd = [path, "--keep-logs", "--outdir", tmp, tex_path]
        else:
            cmd = [path, "-interaction=nonstopmode", "-output-directory", tmp, tex_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        log_path = os.path.join(tmp, "measure.log")
        log = ""
        if os.path.exists(log_path):
            with open(log_path, encoding="utf-8", errors="replace") as f:
                log = f.read()
    got: Dict[int, int] = {int(i): int(n) for i, n in _ARTLINES.findall(log)}
    if len(got) != len(texts) or any(n < 1 for n in got.values()):
        tail = (log or (result.stdout or "") + (result.stderr or ""))[-2000:]
        raise RuntimeError(
            f"{name} reported {len(got)} of {len(texts)} bullet line counts "
            f"(exit {result.returncode}):\n{tail}")
    return [got[i] for i in range(len(texts))]


def bullet_lines(texts: Sequence[str], *,
                 measurer: Callable[[Sequence[str]], List[int]] = measure_lines,
                 template: Optional[str] = None) -> List[int]:
    """Rendered lines per text, from the cache where possible.

    Hits never compile. Misses (deduplicated) go to `measurer` in one call and
    are stored. Blank texts are 0: the formatter drops them. A failed store is
    logged and the measured values are still returned, so a read-only database
    only costs a recompile next time.
    """
    texts = list(texts)
    rendered = [render_bullet_tex(t) for t in texts]
    hashes = [_text_hash(r) if r else "" for r in rendered]
    tpl = template or template_hash()

    known: Dict[str, int] = {}
    wanted = sorted({h for h in hashes if h})
    with Session(db.engine) as session:
        for start in range(0, len(wanted), 500):
            chunk = wanted[start:start + 500]
            rows = session.exec(select(BlockLineCache).where(
                BlockLineCache.template_hash == tpl,
                BlockLineCache.text_hash.in_(chunk))).all()
            known.update({r.text_hash: r.lines for r in rows})

    misses: Dict[str, str] = {}
    for text, h in zip(texts, hashes):
        if h and h not in known and h not in misses:
            misses[h] = text
    if misses:
        measured = list(measurer(list(misses.values())))
        if len(measured) != len(misses):
            raise RuntimeError(f"measurer returned {len(measured)} counts for {len(misses)} texts")
        fresh = dict(zip(misses, measured))
        known.update(fresh)
        try:
            eng = engine_id()
            with Session(db.engine) as session:
                for h, n in fresh.items():
                    session.merge(BlockLineCache(
                        text_hash=h, template_hash=tpl, lines=int(n), engine=eng,
                        measured_at=datetime.utcnow()))
                session.commit()
        except Exception as exc:  # a cache write must never fail the caller
            logger.warning("render cache: could not store %d rows: %s", len(fresh), exc)

    return [known[h] if h else 0 for h in hashes]


def content_bullet_lines(content: Dict, *,
                         measurer: Callable[[Sequence[str]], List[int]] = measure_lines,
                         ) -> Dict[str, int]:
    """`{bullet id: rendered lines}` for every block of tailored `content`."""
    texts = bullet_texts(content)
    return dict(zip(texts, bullet_lines(list(texts.values()), measurer=measurer)))


def page_budget(content: Dict, *, budget: float = PAGE_LINE_BUDGET,
                education_entries: Optional[int] = None,
                measurer: Callable[[Sequence[str]], List[int]] = measure_lines,
                ) -> Dict:
    """`agents.checks.page_line_budget` with measured bullet lines, and skills
    lines counted after the formatter's category merge."""
    skill_lines = None
    if content.get("skills_ranked"):
        cats = {ResumeFormatterAgent._normalize_category(s.get("category") or "Other")
                for s in content["skills_ranked"]
                if s.get("name") and not should_reject_skill(s["name"])}
        skill_lines = len(cats)
    return page_line_budget(content, content_bullet_lines(content, measurer=measurer),
                            budget, skill_lines=skill_lines,
                            education_entries=education_entries)
