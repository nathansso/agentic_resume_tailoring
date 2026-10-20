"""Who wrote each mapped definition, by git blame (#398, ported from #397's `attribution.py`).

- **Ownership.** `git blame -w -M -C --line-porcelain` on the files behind the top-ranked
  definitions: for each definition, the share of its surviving lines the user wrote, the
  leading other author and their commit with the most lines in the span.
- **The introducer.** For the best-ranked definitions only, the oldest commit in
  `git log -L <span>:<path>`: who first wrote those lines.
- **Built by other authors.** Top-ranked, non-test definitions whose surviving lines are under
  20% the user's and that the user did not introduce: what not to credit to them.

Bounded as the eval measured it: at most `MAX_FILES` files blamed, each at most `MAX_LINES`
lines, `MAX_INTRO` `git log -L` lookups, and the blames run in parallel. Never `--since` (it
invents boundary owners). The caller skips all of it in a single-author repo. Author names
appear in a dossier; email addresses never do.
"""

from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from harness.dossier.gitread import Snapshot
from harness.dossier.identity import Identity

MAX_FILES = 40
MAX_LINES = 6_000
MAX_INTRO = 25
WORKERS = 8
FLAGS = ("-w", "-M", "-C")
TESTS = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.\w+$|\.(test|spec)\.\w+$")
_SHA_LINE = re.compile(r"^([0-9a-f]{40}) (\d+) (\d+)(?: (\d+))?$")


@dataclass
class BlameLine:
    sha: str
    name: str
    email: str


def blame(snap: Snapshot, path: str) -> List[BlameLine]:
    """One entry per line of `path` at the built commit: the commit and author that last
    wrote it, with `.mailmap` applied."""
    raw = snap.git("-c", f"mailmap.blob={snap.commit}:.mailmap", "blame", "--line-porcelain",
                   *FLAGS, snap.commit, "--", path)
    out: List[BlameLine] = []
    sha = name = email = ""
    for line in raw.splitlines():
        m = _SHA_LINE.match(line)
        if m:
            sha = m.group(1)
            continue
        if line.startswith("author "):
            name = line[7:]
        elif line.startswith("author-mail "):
            email = line[12:].strip("<>").lower()
        elif line.startswith("\t"):
            out.append(BlameLine(sha, name, email))
    return out


@dataclass
class Ownership:
    """Who wrote the surviving lines of one definition."""
    user_share: float
    lines: int
    top_author: str              # the leading author other than the user ("" if none)
    top_share: float
    top_sha: str                 # that author's commit with the most lines in the span
    user_sha: str                # the user's commit with the most lines in the span
    intro: Optional[Tuple[str, str, bool]] = None   # (short sha, author, is the user)

    def tag(self) -> str:
        if self.lines == 0:
            return ""
        you = round(100 * self.user_share)
        intro = ""
        if self.intro:
            intro = "; introduced by you" if self.intro[2] else f"; introduced by {self.intro[1]}"
            intro += f" {self.intro[0]}"
        if self.user_share >= 0.5:
            return f"[you {you}%, {self.user_sha[:7]}{intro}]"
        if self.top_author:
            extra = f"; you {you}%" if you else ""
            return (f"[mostly {self.top_author} {round(100 * self.top_share)}%, "
                    f"{self.top_sha[:7]}{extra}{intro}]")
        return f"[you {you}%{intro}]"


@dataclass
class BlameIndex:
    """Blame results for one build, bounded. `prefetch` blames the files behind the best-ranked
    definitions in parallel; a file outside that set is never blamed."""
    snap: Snapshot
    who: Identity
    max_files: int = MAX_FILES
    max_lines: int = MAX_LINES
    max_intro: int = MAX_INTRO
    files: Dict[str, Optional[List[BlameLine]]] = field(default_factory=dict)
    intros: Dict[tuple, Optional[Tuple[str, str, bool]]] = field(default_factory=dict)

    def prefetch(self, ranked_paths: Iterable[str]) -> None:
        """Pick, in rank order, the first `max_files` distinct files of at most `max_lines`
        lines, and blame them in parallel. Every other file is recorded as not blamed."""
        chosen: List[str] = []
        for path in ranked_paths:
            if path in self.files or path in chosen:
                continue
            if len(chosen) >= self.max_files:
                break
            if self.snap.line_count(path) <= self.max_lines:
                chosen.append(path)
            else:
                self.files[path] = None
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for path, lines in zip(chosen, pool.map(lambda p: blame(self.snap, p), chosen)):
                self.files[path] = lines

    def lines_for(self, path: str) -> Optional[List[BlameLine]]:
        return self.files.get(path)

    def introducer(self, path: str, start: int, end: int) -> Optional[Tuple[str, str, bool]]:
        """The oldest commit in `git log -L` for the span: who first wrote these lines."""
        key = (path, start, end)
        if key not in self.intros:
            if len(self.intros) >= self.max_intro:
                return None
            raw = self.snap.git("-c", f"mailmap.blob={self.snap.commit}:.mailmap", "log",
                                f"-L{start + 1},{end + 1}:{path}", "--no-patch",
                                "--format=@@%H%x1f%aN%x1f%aE", self.snap.commit)
            heads = [ln[2:].split("\x1f", 2) for ln in raw.splitlines() if ln.startswith("@@")]
            heads = [h for h in heads if len(h) == 3]
            self.intros[key] = ((heads[-1][0][:7], heads[-1][1],
                                 self.who.matches(heads[-1][1], heads[-1][2])) if heads else None)
        return self.intros[key]

    def ownership(self, path: str, start: int, end: int) -> Optional[Ownership]:
        lines = self.lines_for(path)
        if not lines:
            return None
        span = lines[start:end + 1]
        if not span:
            return None
        mine_flags = [self.who.matches(b.name, b.email) for b in span]
        mine = [b for b, m in zip(span, mine_flags) if m]
        theirs = [b for b, m in zip(span, mine_flags) if not m]
        others = Counter(b.name for b in theirs)

        def best_sha(rows):
            c = Counter(b.sha for b in rows)
            return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if c else ""

        top, top_n = "", 0
        if others:  # ties between authors break by name, so the tag is reproducible
            top_n = max(others.values())
            top = sorted(a for a, k in others.items() if k == top_n)[0]
        return Ownership(len(mine) / len(span), len(span), top, top_n / len(span),
                         best_sha([b for b in theirs if b.name == top]), best_sha(mine))

    def annotate(self, path: str, name: str, start: int, end: int) -> str:
        own = self.ownership(path, start, end)
        if own is None:
            return ""
        own.intro = self.introducer(path, start, end)
        return own.tag()


def others_section(ranked: List[Tuple[str, str, int, int]], index: BlameIndex,
                   consider: int = 120, show: int = 25, min_lines: int = 3) -> str:
    """Top-ranked definitions that are mostly another author's surviving lines."""
    rows = []
    for path, name, start, end in ranked[:consider]:
        if TESTS.search(path):
            continue
        own = index.ownership(path, start, end)
        if own is None or own.lines < min_lines or own.user_share >= 0.2 or not own.top_author:
            continue
        intro = index.introducer(path, start, end)
        if intro and intro[2]:  # the user wrote it first; others rewrote it since
            continue
        rows.append(f"  {path}:L{start + 1}-{end + 1} {name}: {own.top_author} "
                    f"{round(100 * own.top_share)}% ({own.top_sha[:7]})"
                    + (f"; you {round(100 * own.user_share)}%" if own.user_share else "")
                    + (f"; introduced by {intro[1]} {intro[0]}" if intro else ""))
        if len(rows) >= show:
            break
    if not rows:
        return "## Built by other authors\n(no top-ranked definition is mostly another author's)"
    return ("## Built by other authors (top-ranked definitions whose surviving lines are mostly "
            "someone else's, by git blame; do not credit these to the user)\n" + "\n".join(rows))


__all__ = ["BlameIndex", "Ownership", "others_section", "TESTS"]
