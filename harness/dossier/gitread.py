"""Reading a repository at one commit, through git only (#398).

The dossier is built from git's objects at a pinned commit, never from a working tree, so a
Windows checkout (CRLF line endings, symlinks written as one-line files) and a Linux one give
the same bytes (#397 § 5, each a measured failure):

- **Text** is a blob's bytes with CRLF normalised to LF.
- **Sizes** are `git ls-tree -l`'s, the committed size.
- **Symlinks** (mode 120000) are resolved through git to the tracked file they point at; one
  that leaves the repo or points at nothing tracked is dropped.

Every git call runs with the user's and the system's git config switched off
(`GIT_CONFIG_GLOBAL`, `GIT_CONFIG_NOSYSTEM`), so a machine's `diff.renames`, `log.*` or
`mailmap.file` can't change the output, and with `core.quotepath=off` so paths come back as
written. Nothing here reaches the network: fetching a clone is `harness/github.py`'s job.
"""

from __future__ import annotations

import os
import posixpath
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

SKIP_DIRS = {".git", "node_modules", "dist", "build", "vendor", "__pycache__", ".next", ".venv",
             "venv", "coverage", ".vscode", ".idea", ".pytest_cache", ".mypy_cache", "out",
             ".turbo", ".cache", "site-packages"}
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock",
              "go.sum", "Cargo.lock", ".DS_Store", "Pipfile.lock", "bun.lockb"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".pdf", ".zip", ".gz",
              ".tgz", ".woff", ".woff2", ".ttf", ".eot", ".otf", ".mp3", ".mp4", ".mov", ".webm",
              ".wav", ".parquet", ".pkl", ".pickle", ".pt", ".pth", ".bin", ".h5", ".npy", ".npz",
              ".db", ".sqlite", ".sqlite3", ".jar", ".exe", ".dll", ".so", ".dylib", ".xlsx",
              ".xls", ".docx", ".pptx", ".joblib", ".onnx", ".pb", ".ckpt", ".safetensors"}
DATA_EXT = {".csv", ".tsv", ".jsonl", ".ndjson", ".parquet"}
CODE_EXT = {".py", ".ipynb", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".go", ".rs",
            ".java", ".kt", ".rb", ".php", ".c", ".h", ".cpp", ".hpp", ".cs", ".swift", ".r",
            ".R", ".scala", ".sql", ".sh", ".html", ".css", ".scss", ".vue", ".svelte", ".jac"}
MARKUP = {".html", ".css", ".scss"}
NOT_SOURCE = re.compile(r"(^|/)(docs?|examples?|fixtures?|__snapshots__)/|\.min\.(js|css)$|"
                        r"(^|/)(LICENSE|CHANGELOG|CONTRIBUTING)", re.I)
FILE_CHARS = 60_000          # one file's text, at most
SHA = re.compile(r"^[0-9a-f]{40}$")


class GitError(Exception):
    """A git command failed. The message is git's own stderr, trimmed."""


def git_env() -> Dict[str, str]:
    """The environment every read runs in: no user or system config, no prompts."""
    env = dict(os.environ)
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
               GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_COUNT"):
        env.pop(key, None)
    return env


def ext(path: str) -> str:
    return Path(path).suffix.lower()


@dataclass
class Entry:
    mode: str
    sha: str
    size: int
    path: str


@dataclass
class Snapshot:
    """One repository at one commit. `files` are the tracked files after filtering, sorted;
    `links` maps each tracked symlink to the tracked file it points at (None: dropped)."""
    repo: Path
    commit: str
    entries: Dict[str, Entry]
    links: Dict[str, Optional[str]]
    files: List[str]
    _text: Dict[str, str] = field(default_factory=dict, repr=False)

    # ── git ──────────────────────────────────────────────────────────────────

    def git(self, *args: str, check: bool = False) -> str:
        return run_git(self.repo, *args, check=check)

    # ── files ────────────────────────────────────────────────────────────────

    def real(self, path: str) -> str:
        """A symlink's target, else the path itself."""
        return self.links.get(path) or path

    def size(self, path: str) -> int:
        entry = self.entries.get(self.real(path))
        return entry.size if entry else 0

    def read_text(self, path: str) -> str:
        """The committed text, CRLF normalised; "" for a binary blob."""
        real = self.real(path)
        if real not in self._text:
            self.prefetch([real])
        return self._text.get(real, "")

    def prefetch(self, paths: Iterable[str]) -> None:
        """Read many blobs in one `git cat-file --batch`."""
        want = []
        for p in paths:
            real = self.real(p)
            if real not in self._text and real in self.entries and real not in want:
                want.append(real)
        if not want:
            return
        for path, data in zip(want, cat_blobs(self.repo, [self.entries[p].sha for p in want])):
            self._text[path] = decode(data)

    def forget(self, paths: Iterable[str]) -> None:
        for p in paths:
            self._text.pop(self.real(p), None)

    def line_count(self, path: str) -> int:
        text = self.read_text(path)
        return text.count("\n") + (0 if not text or text.endswith("\n") else 1)


def run_git(repo: Path, *args: str, check: bool = False, input_bytes: Optional[bytes] = None,
            binary: bool = False):
    done = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off", *args],
                          capture_output=True, env=git_env(), input=input_bytes)
    if check and done.returncode != 0:
        raise GitError(done.stderr.decode("utf-8", errors="replace").strip()[:500]
                       or f"git {args[0]} failed")
    if binary:
        return done.stdout
    return done.stdout.decode("utf-8", errors="replace")


def decode(data: bytes) -> str:
    if b"\x00" in data[:4096]:
        return ""
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


def cat_blobs(repo: Path, shas: Sequence[str]) -> Iterator[bytes]:
    """Each blob's bytes, in order, from one `git cat-file --batch`."""
    out = run_git(repo, "cat-file", "--batch", input_bytes=("\n".join(shas) + "\n").encode(),
                  binary=True, check=True)
    pos = 0
    for _ in shas:
        nl = out.index(b"\n", pos)
        header = out[pos:nl].split()
        if len(header) < 3 or header[1] == b"missing":
            pos = nl + 1
            yield b""
            continue
        size = int(header[2])
        start = nl + 1
        yield out[start:start + size]
        pos = start + size + 1


def resolve_commit(repo: Path, rev: str = "HEAD") -> str:
    sha = run_git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").strip()
    if not SHA.match(sha):
        raise GitError(f"no commit {rev!r} in {repo}")
    return sha


def _entries(repo: Path, commit: str) -> Dict[str, Entry]:
    out: Dict[str, Entry] = {}
    for line in run_git(repo, "ls-tree", "-r", "-l", "-z", commit, check=True).split("\0"):
        if not line:
            continue
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) != 4 or parts[1] != "blob":
            continue                                     # submodules (commit) are skipped
        out[path] = Entry(parts[0], parts[2], int(parts[3]) if parts[3].isdigit() else 0, path)
    return out


def _links(repo: Path, entries: Dict[str, Entry]) -> Dict[str, Optional[str]]:
    links = {p: e for p, e in entries.items() if e.mode == "120000"}
    if not links:
        return {}
    out: Dict[str, Optional[str]] = {}
    targets = cat_blobs(repo, [e.sha for e in links.values()])
    for (path, _), raw in zip(links.items(), targets):
        target = raw.decode("utf-8", errors="replace").strip()
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
        ok = (not resolved.startswith("..") and resolved in entries
              and entries[resolved].mode != "120000")
        out[path] = resolved if ok else None
    return out


def snapshot(repo: Path, rev: str = "HEAD") -> Snapshot:
    """The repository at `rev`: its commit, tracked blobs, symlinks and filtered file list
    (no vendored, generated or lock files; no symlink that doesn't reach a tracked file)."""
    repo = Path(repo)
    commit = resolve_commit(repo, rev)
    entries = _entries(repo, commit)
    links = _links(repo, entries)
    files = []
    for path in entries:
        parts = path.split("/")
        if any(p in SKIP_DIRS for p in parts[:-1]) or parts[-1] in SKIP_NAMES:
            continue
        if path in links and links[path] is None:
            continue
        files.append(path)
    return Snapshot(repo, commit, entries, links, sorted(files))


def file_as_text(snap: Snapshot, path: str, code_only: bool = False,
                 notebook_output_chars: int = 1_500, file_chars: int = FILE_CHARS) -> str:
    if ext(path) in DATA_EXT:
        return "" if code_only else data_summary(snap, path)
    raw = snap.read_text(path)
    if path.lower().endswith(".ipynb"):
        raw = notebook_text(raw, notebook_output_chars, code_only=code_only)
    if len(raw) > file_chars:
        raw = raw[:file_chars] + f"\n…[file truncated at {file_chars} chars]"
    return raw


def data_summary(snap: Snapshot, path: str) -> str:
    """A data file shown by its shape, not its rows."""
    lines = snap.read_text(path).split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    head = [h.rstrip()[:300] for h in lines[:3] if h]
    return f"(data file, ~{len(lines)} lines; first lines:)\n" + "\n".join(head)


def notebook_text(raw: str, output_chars: int, code_only: bool = False) -> str:
    """A notebook as text: markdown cells, code cells and (truncated) text outputs."""
    import json
    try:
        nb = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw[:output_chars]
    if not isinstance(nb, dict):
        return raw[:output_chars]
    parts = []
    for cell in nb.get("cells", []):
        src = "".join(cell.get("source", []))
        kind = cell.get("cell_type")
        if kind == "code":
            parts.append(src if code_only else f"# %% [code]\n{src}")
            if code_only:
                continue
            outs = []
            for o in cell.get("outputs", []):
                if o.get("output_type") == "stream":
                    outs.append("".join(o.get("text", [])))
                elif "data" in o and "text/plain" in o["data"]:
                    outs.append("".join(o["data"]["text/plain"]))
            out = "\n".join(outs).strip()
            if out:
                if len(out) > output_chars:
                    out = out[:output_chars] + " …[output truncated]"
                parts.append(f"# [output]\n{out}")
        elif kind == "markdown" and not code_only:
            parts.append(f"# %% [markdown]\n{src}")
    return "\n\n".join(parts)


def is_source(path: str) -> bool:
    """A file the symbol map reads: code, not markup, not docs or fixtures."""
    e = ext(path)
    return e in CODE_EXT and e not in MARKUP and not NOT_SOURCE.search(path)


__all__: Tuple[str, ...] = ("Snapshot", "GitError", "snapshot", "run_git", "resolve_commit",
                            "file_as_text", "is_source", "git_env")
