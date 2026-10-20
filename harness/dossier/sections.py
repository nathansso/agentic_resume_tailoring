"""The dossier's text sections before the symbol map (#398, ported from #397's ladder).

README, metadata (manifests, imports, committed language shares, dates), the filtered tree with
committed sizes, and the user's authorship. Each line names its source: a path, or a short
SHA for a commit. The manifest list and the Python import parser come from the legacy
`ingestion/github.py`, copied rather than imported: that module imports an HTTP client and
`config`, which the harness may not (`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Set, Tuple

from harness.dossier.gitread import (CODE_EXT, SKIP_DIRS, SKIP_NAMES, Snapshot, ext,
                                     file_as_text)
from harness.dossier.identity import Identity

# The legacy fetcher's manifest list (ingestion/github.py), plus the JS/Go/Rust/Java ones.
MANIFESTS = ["requirements.txt", "setup.py", "pyproject.toml", "Pipfile", "environment.yml",
             "package.json", "Cargo.toml", "go.mod", "requirements-dev.txt", "setup.cfg",
             "pom.xml", "build.gradle", "Gemfile", "Dockerfile", "docker-compose.yml",
             "vercel.json", "railway.json", "netlify.toml", "tsconfig.json", "next.config.js",
             "next.config.mjs", "vite.config.ts", "vitest.config.ts"]
STDLIB = {"os", "sys", "re", "json", "math", "random", "datetime", "collections", "itertools",
          "functools", "pathlib", "typing", "abc", "io", "time", "copy", "warnings", "logging",
          "unittest", "string", "textwrap", "csv", "hashlib", "struct", "operator",
          "contextlib", "tempfile", "glob", "shutil", "pickle", "subprocess", "threading",
          "multiprocessing", "socket", "http", "urllib", "email", "html", "xml", "pdb",
          "traceback", "inspect", "dis", "gc", "weakref", "enum", "dataclasses", "statistics",
          "decimal", "fractions", "numbers", "zipfile", "getpass", "argparse", "uuid",
          "sqlite3", "base64", "codecs", "configparser", "pprint", "heapq", "queue", "signal",
          "ctypes", "platform", "site"}


@dataclass
class Limits:
    """The per-section caps, before the budget fits them (#397's `big` budgets)."""
    readme_chars: int = 40_000
    manifest_chars: int = 3_000
    manifests_max: int = 12
    tree_lines: int = 600
    author_files: int = 120
    author_messages: int = 200


# ── README ───────────────────────────────────────────────────────────────────

def readme_section(snap: Snapshot, lim: Limits) -> str:
    candidates = [f for f in snap.files if "/" not in f and f.lower().startswith("readme")]
    candidates.sort(key=lambda f: (not f.lower().endswith(".md"), f))
    if not candidates:
        return "## README\n(no README in this repository)"
    text = snap.read_text(candidates[0])          # a symlinked README reads its target
    if len(text) > lim.readme_chars:
        text = text[:lim.readme_chars] + f"\n…[README truncated at {lim.readme_chars} chars]"
    return f"## README ({candidates[0]})\n{text.strip()}"


# ── metadata ─────────────────────────────────────────────────────────────────

_JS_IMPORT = re.compile(r"""(?:import\s[^'"]*?from\s*|import\s*\(?\s*|require\s*\(\s*)['"]([^'"./][^'"]*)['"]""")
_GO_IMPORT = re.compile(r'^\s*(?:import\s+)?"([\w.\-]+/[\w.\-/]+)"', re.M)
_PY_IMPORT = re.compile(r"^import\s+([\w.]+)")
_PY_FROM = re.compile(r"^from\s+([\w.]+)\s+import")
IMPORT_EXT = {".py", ".ipynb", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go"}
_CHUNK = 400


def python_imports(code: str) -> Set[str]:
    """Top-level packages a Python file imports, standard library left out."""
    found = set()
    for line in code.split("\n"):
        line = line.strip()
        for rx in (_PY_IMPORT, _PY_FROM):
            m = rx.match(line)
            if m:
                found.add(m.group(1).split(".")[0])
    return {p for p in found if p and p not in STDLIB}


def imports_of(snap: Snapshot) -> List[str]:
    seen: Counter = Counter()
    files = [f for f in snap.files if ext(f) in IMPORT_EXT and f not in snap.links]
    for i in range(0, len(files), _CHUNK):           # bounded memory on a large repo
        chunk = files[i:i + _CHUNK]
        snap.prefetch(chunk)
        for f in chunk:
            code = file_as_text(snap, f, code_only=True)
            e = ext(f)
            if e in {".py", ".ipynb"}:
                names = python_imports(code)
            elif e == ".go":
                names = set(_GO_IMPORT.findall(code))
            else:
                names = {("/".join(m.split("/")[:2]) if m.startswith("@") else m.split("/")[0])
                         for m in _JS_IMPORT.findall(code)}
                names = {n for n in names if not n.startswith("node:")}
            seen.update(sorted(names))  # sorted: ties keep a reproducible order
        snap.forget(chunk)
    return [n for n, _ in seen.most_common(60)]


def language_bytes(snap: Snapshot) -> Dict[str, int]:
    """Committed bytes by extension, the 8 largest."""
    sizes: Counter = Counter()
    for f in snap.files:
        if f in snap.links:
            continue                                  # counted once, under its target
        e = ext(f)
        if e in CODE_EXT:
            sizes[e.lstrip(".")] += snap.size(f)
    return dict(sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0]))[:8])


def metadata_section(snap: Snapshot, meta: Optional[Mapping], lim: Limits) -> str:
    """GitHub's description, topics, homepage and license when given; languages by committed
    bytes; the first and last commit dates; dependency manifests; detected imports. Stars are
    left out: they change without a commit, and a dossier is a function of its commit."""
    lines = ["## Metadata"]
    meta = meta or {}
    if meta.get("description"):
        lines.append(f"Description: {meta['description']}")
    if meta.get("topics"):
        lines.append("Topics: " + ", ".join(sorted(meta["topics"])))
    langs = language_bytes(snap)
    if langs:
        total = sum(langs.values()) or 1
        lines.append("Languages (committed bytes): "
                     + ", ".join(f"{k} {100 * v / total:.0f}%" for k, v in langs.items()))
    first = snap.git("log", "--reverse", "--format=%as", "--max-parents=0", snap.commit).split()
    last = snap.git("log", "-1", "--format=%as", snap.commit).strip()
    if first or last:
        lines.append(f"Dates: first commit {first[0] if first else ''}, "
                     f"last commit {last} ({snap.commit[:7]})")
    for key in ("homepage", "license"):
        if meta.get(key):
            lines.append(f"{key.capitalize()}: {meta[key]}")
    found = 0
    for f in snap.files:
        if found >= lim.manifests_max:
            break
        if Path(f).name in MANIFESTS and f.count("/") <= 2:
            text = snap.read_text(f)
            if len(text) > lim.manifest_chars:
                text = text[:lim.manifest_chars] + "\n…[truncated]"
            lines.append(f"\n### {f}\n{text.strip()}")
            found += 1
    imports = imports_of(snap)
    if imports:
        lines.append("\nDetected imports (most used first): " + ", ".join(imports))
    return "\n".join(lines)


# ── file tree ────────────────────────────────────────────────────────────────

def tree_section(snap: Snapshot, lim: Limits) -> str:
    """Paths with committed sizes; past the line cap, the deepest directories collapse to file
    counts."""
    files = snap.files

    def size(f):
        n = snap.size(f)
        return f"{n / 1024:.0f}K" if n >= 1024 else f"{n}B"

    lines = [f"{f}  ({size(f)})" for f in files]
    if len(lines) > lim.tree_lines:
        depth = max(f.count("/") for f in files)
        while len(lines) > lim.tree_lines and depth > 0:
            groups: Dict[str, list] = defaultdict(list)
            for f in files:
                parts = f.split("/")
                key = "/".join(parts[:depth]) + "/" if len(parts) > depth else f
                groups[key].append(f)
            lines = [k if len(v) == 1 and not k.endswith("/") else f"{k}  ({len(v)} files)"
                     for k, v in sorted(groups.items())]
            depth -= 1
        lines = lines[:lim.tree_lines]
    return f"## File tree ({len(files)} files after filtering)\n" + "\n".join(lines)


# ── authorship ───────────────────────────────────────────────────────────────

@dataclass
class Authorship:
    user_commits: int
    total_commits: int
    authors: int                         # distinct people, the user counted once
    first: str
    last: str
    files: Dict[str, dict]               # path -> {"added", "deleted", "commits", "last"}
    messages: List[Tuple[str, str, str]]  # (date, short sha, subject), newest first

    def weights(self) -> Dict[str, float]:
        return {p: float(d["added"] + d["deleted"]) for p, d in self.files.items()}

    @property
    def multi_author(self) -> bool:
        return self.authors > 1 and self.user_commits > 0


_RENAME = re.compile(r"\{[^{}]*=> ([^{}]*)\}")


def authorship(snap: Snapshot, who: Identity) -> Authorship:
    """The user's non-merge commits, matched by identity (not `--author`, a regex that also
    matches longer addresses), with numstat per file; renames credit the new path."""
    raw = snap.git("-c", f"mailmap.blob={snap.commit}:.mailmap", "log", "--no-merges", "-M",
                   "--numstat", "--format=@@%H%x1f%as%x1f%aN%x1f%aE%x1f%an%x1f%ae%x1f%s",
                   snap.commit)
    files: Dict[str, dict] = {}
    messages: List[Tuple[str, str, str]] = []
    people: Set[str] = set()
    total, mine, date = 0, False, ""
    for line in raw.splitlines():
        if line.startswith("@@"):
            parts = line[2:].split("\x1f", 6)
            if len(parts) != 7:
                mine = False
                continue
            sha, date, mname, memail, rname, remail, subject = parts
            total += 1
            mine = who.matches(mname, memail) or who.matches(rname, remail)
            people.add("\0user" if mine else (memail.lower() or mname))
            if mine:
                messages.append((date, sha[:7], subject))
            continue
        if not mine:
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        if "=>" in path:  # a rename: credit the new path
            path = _RENAME.sub(r"\1", path).replace("//", "/")
            path = path.split(" => ")[-1]
        d = files.setdefault(path, {"added": 0, "deleted": 0, "commits": 0, "last": date})
        d["added"] += int(added) if added.isdigit() else 0
        d["deleted"] += int(deleted) if deleted.isdigit() else 0
        d["commits"] += 1
    dates = sorted(m[0] for m in messages)
    return Authorship(len(messages), total, len(people), dates[0] if dates else "",
                      dates[-1] if dates else "", files, messages)


def authorship_section(a: Authorship, user_label: str, files_now: Set[str], lim: Limits) -> str:
    lines = [f"## Authorship ({user_label})"]
    if not a.user_commits:
        lines.append(f"No commits by the user found ({a.total_commits} commits, "
                     f"{a.authors} author(s)).")
        return "\n".join(lines)
    share = 100 * a.user_commits / max(1, a.total_commits)
    lines.append(f"User commits: {a.user_commits} of {a.total_commits} non-merge commits "
                 f"({share:.0f}%), {a.authors} author(s) in total; user active {a.first} to "
                 f"{a.last}.")
    ranked = sorted(a.files.items(), key=lambda kv: (-(kv[1]["added"] + kv[1]["deleted"]), kv[0]))
    shown = [(p, d) for p, d in ranked
             if Path(p).name not in SKIP_NAMES and not any(s in p.split("/") for s in SKIP_DIRS)]
    lines.append(f"Files the user changed ({len(shown)}; top {min(len(shown), lim.author_files)} "
                 "by lines changed; +added -deleted, commits, last change):")
    for p, d in shown[:lim.author_files]:
        gone = "" if p in files_now else "  [no longer in the repo]"
        lines.append(f"  {p}  +{d['added']} -{d['deleted']}  {d['commits']} commit(s)  "
                     f"{d['last']}{gone}")
    seen, msgs = set(), []
    for date, sha, subject in a.messages:
        if subject.lower() in seen:
            continue
        seen.add(subject.lower())
        msgs.append(f"  {date} {sha}  {subject[:160]}")
    lines.append(f"Commit messages (newest first, {min(len(msgs), lim.author_messages)} of "
                 f"{len(msgs)} distinct):")
    lines.extend(msgs[:lim.author_messages])
    return "\n".join(lines)
