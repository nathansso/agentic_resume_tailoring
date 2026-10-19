"""The dossier ladder for the repo-map sufficiency spike (issue #397).

Given a local clone, the user's git author emails and (optionally) the GitHub metadata, build
the seven cumulative inputs a writer gets, from the README alone to the full code:

  1  README
  2  + metadata (languages, topics, dates), dependency manifests and imports
  3  + a filtered file tree
  4  + authorship: the user's changed files, line counts and dates, and commit messages
  5  + a symbol map seeded by the user's files (`symbols.py`); `5r` is the same map built with
       the regex fallback instead of tree-sitter
  6  + the user's most-changed source files
  7  README, metadata, tree and authorship + the full code (capped; the cap is recorded)

Everything here is offline and deterministic: it reads the clone with `git` and the filesystem.
The legacy `ingestion/github.py` fetches the same kinds of things over the API; its manifest
list and Python import parser are reused.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from eval.repo_dossier.symbols import build_symbol_map

RUNGS = ["1", "2", "3", "4", "5", "5r", "6", "7"]
RUNG_NAMES = {
    "1": "README", "2": "+ metadata & deps", "3": "+ file tree", "4": "+ authorship",
    "5": "+ symbol map (tree-sitter)", "5r": "+ symbol map (regex)", "6": "+ sampled files",
    "7": "full code",
}

# The legacy fetcher's manifest list (ingestion/github.py), plus the JS/Go/Rust/Java ones.
try:
    from ingestion.github import DEPENDENCY_FILES as _LEGACY_DEPS
except Exception:  # keep the builder importable without the app's config
    _LEGACY_DEPS = ["requirements.txt", "setup.py", "pyproject.toml", "Pipfile",
                    "environment.yml", "package.json", "Cargo.toml", "go.mod"]
MANIFESTS = list(dict.fromkeys(_LEGACY_DEPS + [
    "requirements-dev.txt", "setup.cfg", "pom.xml", "build.gradle", "Gemfile",
    "Dockerfile", "docker-compose.yml", "vercel.json", "railway.json", "netlify.toml",
    "tsconfig.json", "next.config.js", "next.config.mjs", "vite.config.ts", "vitest.config.ts",
]))

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
NOT_SOURCE = re.compile(r"(^|/)(docs?|examples?|fixtures?|__snapshots__)/|\.min\.(js|css)$|"
                        r"(^|/)(LICENSE|CHANGELOG|CONTRIBUTING)", re.I)


@dataclass
class Budgets:
    readme_chars: int = 12_000
    manifest_chars: int = 2_000
    manifests_max: int = 8
    tree_lines: int = 250
    author_files: int = 40
    author_messages: int = 60
    symbol_tokens: int = 2_048
    sample_files: int = 3
    sample_chars: int = 12_000
    notebook_output_chars: int = 1_500
    full_code_chars: int = 450_000   # about 130K tokens; the judge gets a larger cap
    file_chars: int = 60_000


@dataclass
class RepoInput:
    path: Path
    name: str                      # "owner/repo"
    user_label: str                # how the writer is told who the user is
    user_emails: list[str]
    meta: dict | None = None       # GitHub metadata, when fetched


@dataclass
class Ladder:
    rungs: dict[str, str]
    sections: dict[str, str]
    stats: dict = field(default_factory=dict)


# ── reading the clone ────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.stdout


def list_files(repo: Path) -> list[str]:
    """Tracked files, minus vendored, generated and lock files."""
    out = []
    for f in _git(repo, "ls-files").splitlines():
        parts = f.split("/")
        if any(p in SKIP_DIRS for p in parts[:-1]) or parts[-1] in SKIP_NAMES:
            continue
        if (repo / f).is_file():
            out.append(f)
    return sorted(out)


_SIZES: dict[str, dict[str, int]] = {}


def file_size(repo: Path, path: str) -> int:
    """A file's size as committed (`git ls-tree -l`), not on disk, where a Windows checkout's CRLF
    makes it larger; on-disk size for anything not in HEAD."""
    key = str(Path(repo).resolve())
    if key not in _SIZES:
        sizes = {}
        for line in _git(Path(repo), "ls-tree", "-r", "-l", "HEAD").splitlines():
            meta, _, name = line.partition("	")
            parts = meta.split()
            if len(parts) == 4 and parts[3].isdigit():
                sizes[name] = int(parts[3])
        _SIZES[key] = sizes
    got = _SIZES[key].get(path)
    return got if got is not None else (Path(repo) / path).stat().st_size


def _ext(path: str) -> str:
    return Path(path).suffix.lower()


def is_text(path: str) -> bool:
    return _ext(path) not in BINARY_EXT


def read_text(repo: Path, path: str) -> str:
    data = (repo / path).read_bytes()
    if b"\x00" in data[:4096]:
        return ""
    # a Windows checkout (core.autocrlf) has CRLF where Linux has LF: normalise, so the dossier
    # is the same bytes whichever way the clone was made
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


def notebook_text(raw: str, output_chars: int, code_only: bool = False) -> str:
    """A notebook as text: markdown cells, code cells and (truncated) text outputs."""
    try:
        nb = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
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


def data_summary(repo: Path, path: str) -> str:
    """A data file shown by its shape, not its rows."""
    try:
        with open(repo / path, encoding="utf-8", errors="replace") as fh:
            head = [next(fh, "") for _ in range(3)]
            rows = 3 + sum(1 for _ in fh)
    except OSError:
        return ""
    head = [h.rstrip()[:300] for h in head if h]
    return f"(data file, ~{rows} lines; first lines:)\n" + "\n".join(head)


def file_as_text(repo: Path, path: str, b: Budgets, code_only: bool = False) -> str:
    if _ext(path) in DATA_EXT:
        return "" if code_only else data_summary(repo, path)
    raw = read_text(repo, path)
    if path.lower().endswith(".ipynb"):
        raw = notebook_text(raw, b.notebook_output_chars, code_only=code_only)
    if len(raw) > b.file_chars:
        raw = raw[:b.file_chars] + f"\n…[file truncated at {b.file_chars} chars]"
    return raw


# ── the sections ─────────────────────────────────────────────────────────────

def readme_section(repo: Path, files: list[str], b: Budgets) -> str:
    candidates = [f for f in files if "/" not in f and f.lower().startswith("readme")]
    candidates.sort(key=lambda f: (not f.lower().endswith(".md"), f))
    if not candidates:
        return "## README\n(no README in this repository)"
    text = read_text(repo, candidates[0])
    if len(text) > b.readme_chars:
        text = text[:b.readme_chars] + f"\n…[README truncated at {b.readme_chars} chars]"
    return f"## README ({candidates[0]})\n{text.strip()}"


_JS_IMPORT = re.compile(r"""(?:import\s[^'"]*?from\s*|import\s*\(?\s*|require\s*\(\s*)['"]([^'"./][^'"]*)['"]""")
_GO_IMPORT = re.compile(r'^\s*(?:import\s+)?"([\w.\-]+/[\w.\-/]+)"', re.M)


def _python_imports(code: str) -> set[str]:
    try:
        from ingestion.github import GitHubIngestor
        return set(GitHubIngestor._parse_imports(code))
    except Exception:
        found = set()
        for line in code.splitlines():
            m = re.match(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", line)
            if m:
                found.add((m.group(1) or m.group(2)).split(".")[0])
        return found


def imports_of(repo: Path, files: list[str], b: Budgets) -> list[str]:
    seen: Counter = Counter()
    for f in files:
        e = _ext(f)
        if e not in {".py", ".ipynb", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go"}:
            continue
        code = file_as_text(repo, f, b, code_only=True)
        if e in {".py", ".ipynb"}:
            names = _python_imports(code)
        elif e == ".go":
            names = set(_GO_IMPORT.findall(code))
        else:
            names = {("/".join(m.split("/")[:2]) if m.startswith("@") else m.split("/")[0])
                     for m in _JS_IMPORT.findall(code)}
            names = {n for n in names if not n.startswith("node:")}
        seen.update(sorted(names))  # sorted: ties keep a reproducible order
    return [n for n, _ in seen.most_common(60)]


def language_bytes(repo: Path, files: list[str]) -> dict[str, int]:
    sizes: Counter = Counter()
    for f in files:
        e = _ext(f)
        if e in CODE_EXT:
            sizes[e.lstrip(".")] += file_size(repo, f)
    return dict(sizes.most_common(8))


def metadata_section(repo: Path, files: list[str], meta: dict | None, b: Budgets) -> str:
    lines = ["## Metadata"]
    meta = meta or {}
    if meta.get("description"):
        lines.append(f"Description: {meta['description']}")
    if meta.get("topics"):
        lines.append("Topics: " + ", ".join(meta["topics"]))
    langs = meta.get("languages") or language_bytes(repo, files)
    if langs:
        total = sum(langs.values()) or 1
        lines.append("Languages: " + ", ".join(f"{k} {100 * v / total:.0f}%" for k, v in langs.items()))
    first = _git(repo, "log", "--reverse", "--format=%as", "--max-parents=0").splitlines()
    last = _git(repo, "log", "-1", "--format=%as").strip()
    created = meta.get("created_at", (first[0] if first else ""))[:10]
    pushed = meta.get("pushed_at", last)[:10]
    if created or pushed:
        lines.append(f"Dates: created {created}, last pushed {pushed}")
    for key in ("homepage", "stars", "license"):
        if meta.get(key):
            lines.append(f"{key.capitalize()}: {meta[key]}")
    found = 0
    for f in files:
        if found >= b.manifests_max:
            break
        if Path(f).name in MANIFESTS and f.count("/") <= 2:
            text = read_text(repo, f)
            if len(text) > b.manifest_chars:
                text = text[:b.manifest_chars] + "\n…[truncated]"
            lines.append(f"\n### {f}\n{text.strip()}")
            found += 1
    imports = imports_of(repo, files, b)
    if imports:
        lines.append("\nDetected imports (most used first): " + ", ".join(imports))
    return "\n".join(lines)


def tree_section(repo: Path, files: list[str], b: Budgets) -> str:
    """Paths with sizes; past the line cap, the deepest directories collapse to file counts."""
    def size(f):
        n = file_size(repo, f)
        return f"{n / 1024:.0f}K" if n >= 1024 else f"{n}B"

    lines = [f"{f}  ({size(f)})" for f in files]
    if len(lines) > b.tree_lines:
        depth = max(f.count("/") for f in files)
        while len(lines) > b.tree_lines and depth > 0:
            groups: dict[str, list] = defaultdict(list)
            for f in files:
                parts = f.split("/")
                key = "/".join(parts[:depth]) + "/" if len(parts) > depth else f
                groups[key].append(f)
            lines = [k if len(v) == 1 and not k.endswith("/") else f"{k}  ({len(v)} files)"
                     for k, v in sorted(groups.items())]
            depth -= 1
        lines = lines[:b.tree_lines]
    return f"## File tree ({len(files)} files after filtering)\n" + "\n".join(lines)


@dataclass
class Authorship:
    user_commits: int
    total_commits: int
    authors: int
    first: str
    last: str
    files: dict[str, dict]          # path -> {"added", "deleted", "commits", "last"}
    messages: list[tuple[str, str, str]]   # (date, sha, subject)

    def weights(self) -> dict[str, float]:
        return {p: float(d["added"] + d["deleted"]) for p, d in self.files.items()}


def authorship(repo: Path, emails: list[str]) -> Authorship:
    author_args = [f"--author={re.escape(e)}" for e in emails]
    all_authors = _git(repo, "log", "--no-merges", "--format=%ae").splitlines()
    total = len(all_authors)
    if not emails:
        return Authorship(0, total, len(set(all_authors)), "", "", {}, [])
    raw = _git(repo, "log", "--no-merges", "--numstat", "--format=@@%h|%as|%s", *author_args)
    files: dict[str, dict] = {}
    messages = []
    date = ""
    for line in raw.splitlines():
        if line.startswith("@@"):
            sha, date, subject = line[2:].split("|", 2)
            messages.append((date, sha, subject))
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        if "=>" in path:  # a rename: credit the new path
            path = re.sub(r"\{[^{}]*=> ([^{}]*)\}", r"\1", path).replace("//", "/")
            path = path.split(" => ")[-1]
        d = files.setdefault(path, {"added": 0, "deleted": 0, "commits": 0, "last": date})
        d["added"] += int(added) if added.isdigit() else 0
        d["deleted"] += int(deleted) if deleted.isdigit() else 0
        d["commits"] += 1
    dates = sorted(m[0] for m in messages)
    return Authorship(len(messages), total, len(set(all_authors)),
                      dates[0] if dates else "", dates[-1] if dates else "", files, messages)


def authorship_section(a: Authorship, user_label: str, files_now: set[str], b: Budgets) -> str:
    lines = [f"## Authorship ({user_label})"]
    if not a.user_commits:
        lines.append(f"No commits by the user found ({a.total_commits} commits, {a.authors} authors).")
        return "\n".join(lines)
    share = 100 * a.user_commits / max(1, a.total_commits)
    lines.append(f"User commits: {a.user_commits} of {a.total_commits} non-merge commits ({share:.0f}%), "
                 f"{a.authors} author(s) in total; user active {a.first} to {a.last}.")
    ranked = sorted(a.files.items(), key=lambda kv: -(kv[1]["added"] + kv[1]["deleted"]))
    shown = [(p, d) for p, d in ranked
             if Path(p).name not in SKIP_NAMES and not any(s in p.split("/") for s in SKIP_DIRS)]
    lines.append(f"Files the user changed ({len(shown)}; top {min(len(shown), b.author_files)} "
                 "by lines changed; +added -deleted, commits, last change):")
    for p, d in shown[:b.author_files]:
        gone = "" if p in files_now else "  [no longer in the repo]"
        lines.append(f"  {p}  +{d['added']} -{d['deleted']}  {d['commits']} commit(s)  {d['last']}{gone}")
    seen, msgs = set(), []
    for date, sha, subject in a.messages:
        if subject.lower() in seen:
            continue
        seen.add(subject.lower())
        msgs.append(f"  {date} {sha}  {subject[:160]}")
    lines.append(f"Commit messages (newest first, {min(len(msgs), b.author_messages)} of {len(msgs)} distinct):")
    lines.extend(msgs[:b.author_messages])
    return "\n".join(lines)


def full_authorship_record(repo: Path) -> str:
    """Every author's commits and files, for the judge (who must check role claims)."""
    raw = _git(repo, "log", "--no-merges", "--numstat", "--format=@@%an <%ae>|%h|%as|%s")
    per_author: dict[str, dict] = defaultdict(lambda: {"commits": [], "files": Counter()})
    who = None
    for line in raw.splitlines():
        if line.startswith("@@"):
            who, sha, date, subject = line[2:].split("|", 3)
            per_author[who]["commits"].append(f"{date} {sha} {subject[:140]}")
            continue
        parts = line.split("\t")
        if len(parts) == 3 and who:
            added, deleted, path = parts
            n = (int(added) if added.isdigit() else 0) + (int(deleted) if deleted.isdigit() else 0)
            per_author[who]["files"][path] += n
    out = ["## Git history by author"]
    for who, d in sorted(per_author.items(), key=lambda kv: -len(kv[1]["commits"])):
        out.append(f"\n### {who}: {len(d['commits'])} commits")
        out.append("Files by lines changed: " + ", ".join(
            f"{p} ({n})" for p, n in d["files"].most_common(60)))
        out.append("Commits:\n" + "\n".join(f"  {c}" for c in d["commits"][:150]))
    return "\n".join(out)


def is_source(path: str) -> bool:
    return _ext(path) in CODE_EXT and not NOT_SOURCE.search(path)


def samples_section(repo: Path, files: list[str], a: Authorship, b: Budgets) -> str:
    present = set(files)
    ranked = sorted(a.files.items(), key=lambda kv: -(kv[1]["added"] + kv[1]["deleted"]))
    picks = [p for p, _ in ranked if p in present and is_source(p)][:b.sample_files]
    if not picks:
        return "## Sampled source files\n(none: the user changed no source files still in the repo)"
    parts = [f"## Sampled source files (the user's {len(picks)} most-changed)"]
    for p in picks:
        text = file_as_text(repo, p, Budgets(file_chars=b.sample_chars, notebook_output_chars=600))
        parts.append(f"### {p}\n{text}")
    return "\n\n".join(parts)


def full_code(repo: Path, files: list[str], a: Authorship, b: Budgets, cap: int,
              skip: set[str] = frozenset()) -> tuple[str, dict]:
    """All text files, the user's first (by lines changed), then the rest by path, to `cap`."""
    weights = a.weights()
    order = sorted((f for f in files if is_text(f) and f not in skip),
                   key=lambda f: (-weights.get(f, 0), f))
    parts, used, included, dropped = [], 0, 0, []
    for f in order:
        text = file_as_text(repo, f, b)
        if not text.strip():
            continue
        block = f"### {f}\n{text}\n"
        if used + len(block) > cap:
            dropped.append(f)
            continue
        parts.append(block)
        used += len(block)
        included += 1
    stats = {"chars": used, "files_included": included, "files_dropped": len(dropped),
             "truncated": bool(dropped)}
    header = "## Full code" + (f" (capped at {cap} chars; {len(dropped)} files left out)" if dropped else "")
    return header + "\n" + "\n".join(parts), stats


def source_map_inputs(repo: Path, files: list[str], b: Budgets) -> dict[str, str]:
    from eval.repo_dossier.symbols import language_of
    return {f: file_as_text(repo, f, b, code_only=True) for f in files
            if language_of(f) and not NOT_SOURCE.search(f)}


# ── the ladder ───────────────────────────────────────────────────────────────

def build_ladder(repo: RepoInput, b: Budgets | None = None) -> Ladder:
    b = b or Budgets()
    path = Path(repo.path)
    files = list_files(path)
    auth = authorship(path, repo.user_emails)
    sources = source_map_inputs(path, files, b)
    smap = build_symbol_map(sources, auth.weights(), b.symbol_tokens, backend="auto")
    rmap = build_symbol_map(sources, auth.weights(), b.symbol_tokens, backend="regex")
    s = {
        "readme": readme_section(path, files, b),
        "metadata": metadata_section(path, files, repo.meta, b),
        "tree": tree_section(path, files, b),
        "authorship": authorship_section(auth, repo.user_label, set(files), b),
        "symbols": f"## Symbol map (ranked by PageRank seeded with the user's files; {smap.backend})\n"
                   + (smap.text or "(no definitions found)"),
        "symbols_regex": "## Symbol map (ranked by PageRank seeded with the user's files; regex)\n"
                         + (rmap.text or "(no definitions found)"),
        "samples": samples_section(path, files, auth, b),
    }
    readme_files = {f for f in files if "/" not in f and f.lower().startswith("readme")}
    code, code_stats = full_code(path, files, auth, b, b.full_code_chars, skip=readme_files)
    s["full"] = code
    order = {
        "1": ["readme"],
        "2": ["readme", "metadata"],
        "3": ["readme", "metadata", "tree"],
        "4": ["readme", "metadata", "tree", "authorship"],
        "5": ["readme", "metadata", "tree", "authorship", "symbols"],
        "5r": ["readme", "metadata", "tree", "authorship", "symbols_regex"],
        "6": ["readme", "metadata", "tree", "authorship", "symbols", "samples"],
        "7": ["readme", "metadata", "tree", "authorship", "full"],
    }
    rungs = {r: "\n\n".join(s[k] for k in keys) for r, keys in order.items()}
    stats = {
        "files": len(files),
        "user_commits": auth.user_commits, "total_commits": auth.total_commits,
        "authors": auth.authors, "user_files": len(auth.files),
        "symbol_backend": smap.backend, "symbol_defs": smap.definitions,
        "symbol_rendered": smap.rendered, "symbol_regex_rendered": rmap.rendered,
        "full_code": code_stats,
        "section_chars": {k: len(v) for k, v in s.items()},
        "rung_chars": {r: len(t) for r, t in rungs.items()},
    }
    return Ladder(rungs, s, stats)


def judge_context(repo: RepoInput, cap: int = 900_000) -> tuple[str, dict]:
    """What the judge checks claims against: README, metadata, the full code to a larger cap,
    and every author's history."""
    b = Budgets(full_code_chars=cap, readme_chars=200_000)
    path = Path(repo.path)
    files = list_files(path)
    auth = authorship(path, repo.user_emails)
    code, stats = full_code(path, files, auth, b, cap)
    text = "\n\n".join([
        metadata_section(path, files, repo.meta, b),
        tree_section(path, files, Budgets(tree_lines=600)),
        full_authorship_record(path),
        code,
    ])
    return text, stats
