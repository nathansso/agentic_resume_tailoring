"""Feature-level attribution for the #397 follow-up variants (`variants.py`).

#397's authorship section is per file: the files the user changed, with line counts, and their
commit messages. In a 51-author repo that let the writer credit the user with every feature in
the file they changed most. These are the deterministic, model-free ways to say who wrote what:

- **Per-symbol ownership by blame.** `git blame -w -M -C --line-porcelain` on the files holding
  the top-ranked definitions; for each definition, the share of its surviving lines each author
  wrote, and the commit with the most of them. Bounded: at most `max_files` files, each at most
  `max_lines` lines, blamed once per build.
- **Commit subjects by file.** Each of the user's changed files with the subjects (and SHAs) of
  the user's commits that touched it, and the Conventional Commits type (`feat`, `fix`, …) where
  a subject has one.
- **Merged pull requests.** The user's merged PRs (title, number, merge date, size, the first
  lines of the body, the files changed), fetched once through `gh` by the eval runner and cached;
  in #398 the GitHub client fetches them.
- **First authorship.** For the best-ranked definitions only (`max_intro`), the oldest commit in
  `git log -L <span>:<path>`: who introduced those lines. Surveys of developers find first
  authorship the strongest single signal of who knows (wrote) a piece of code.
- **Built by others.** The top-ranked definitions whose surviving lines are mostly another
  author's and that the user did not introduce, named with that author and a commit, so the
  writer is told what not to credit.

Author names appear in the dossier; email addresses never do.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

_SHA_LINE = re.compile(r"^([0-9a-f]{40}) (\d+) (\d+)(?: (\d+))?$")
TESTS = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.\w+$|\.(test|spec)\.\w+$")
CONVENTIONAL = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<desc>.+)$")


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.stdout


@dataclass
class BlameLine:
    sha: str
    name: str
    email: str


def blame(repo: Path, path: str, flags: tuple[str, ...] = ("-w", "-M", "-C")) -> list[BlameLine]:
    """One entry per line of `path` at HEAD: the commit and author that last wrote it."""
    raw = _git(repo, "blame", "--line-porcelain", *flags, "HEAD", "--", path)
    out: list[BlameLine] = []
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
    intro: tuple[str, str, bool] | None = None   # (short sha, author name, is the user) of the
                                                  # commit that introduced the span, if looked up

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
            return f"[mostly {self.top_author} {round(100 * self.top_share)}%, {self.top_sha[:7]}{extra}{intro}]"
        return f"[you {you}%{intro}]"


@dataclass
class BlameIndex:
    """Blame results for one build, computed lazily and bounded."""
    repo: Path
    user_emails: set[str]
    max_files: int = 40
    max_lines: int = 6000
    flags: tuple[str, ...] = ("-w", "-M", "-C")
    max_intro: int = 25          # `git log -L` lookups of the introducing commit, best-ranked first
    files: dict[str, list[BlameLine] | None] = field(default_factory=dict)
    intros: dict[tuple, tuple | None] = field(default_factory=dict)

    def introducer(self, path: str, start: int, end: int) -> tuple[str, str, bool] | None:
        """The oldest commit in `git log -L` for the span: who first wrote these lines."""
        key = (path, start, end)
        if key not in self.intros:
            if len(self.intros) >= self.max_intro:
                return None
            raw = _git(self.repo, "log", f"-L{start + 1},{end + 1}:{path}", "--no-patch",
                       "--format=@@%h|%aN|%aE", "HEAD")
            heads = [ln[2:].split("|", 2) for ln in raw.splitlines() if ln.startswith("@@")]
            self.intros[key] = ((heads[-1][0], heads[-1][1], heads[-1][2].lower() in self.user_emails)
                                if heads else None)
        return self.intros[key]

    def lines_for(self, path: str) -> list[BlameLine] | None:
        if path not in self.files:
            if len([v for v in self.files.values() if v is not None]) >= self.max_files:
                self.files[path] = None
            else:
                try:
                    n = sum(1 for _ in open(self.repo / path, encoding="utf-8", errors="replace"))
                except OSError:
                    n = self.max_lines + 1
                self.files[path] = blame(self.repo, path, self.flags) if n <= self.max_lines else None
        return self.files[path]

    def ownership(self, path: str, start: int, end: int) -> Ownership | None:
        lines = self.lines_for(path)
        if not lines:
            return None
        span = lines[start:end + 1]
        if not span:
            return None
        mine = [b for b in span if b.email in self.user_emails]
        others = Counter(b.name for b in span if b.email not in self.user_emails)

        def best_sha(rows):
            c = Counter(b.sha for b in rows)
            return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if c else ""

        top, top_n = "", 0
        if others:  # ties between authors break by name, so the tag is reproducible
            top_n = max(others.values())
            top = sorted(a for a, k in others.items() if k == top_n)[0]
        return Ownership(len(mine) / len(span), len(span), top, top_n / len(span),
                         best_sha([b for b in span if b.name == top and b.email not in self.user_emails]),
                         best_sha(mine))

    def annotate(self, path: str, name: str, start: int, end: int) -> str:
        own = self.ownership(path, start, end)
        if own is None:
            return ""
        own.intro = self.introducer(path, start, end)
        return own.tag()


# ── commit subjects by file ──────────────────────────────────────────────────

def user_commits_by_file(repo: Path, emails: list[str]) -> dict[str, list[tuple[str, str, str]]]:
    """path -> [(date, short sha, subject)], newest first, for the user's non-merge commits."""
    if not emails:
        return {}
    raw = _git(repo, "log", "--no-merges", "--name-only", "--format=@@%h|%as|%s",
               *[f"--author={re.escape(e)}" for e in emails])
    out: dict[str, list] = defaultdict(list)
    cur = None
    for line in raw.splitlines():
        if line.startswith("@@"):
            sha, date, subject = line[2:].split("|", 2)
            cur = (date, sha, subject)
        elif line.strip() and cur:
            out[line.strip()].append(cur)
    return dict(out)


def conventional_type(subject: str) -> str | None:
    m = CONVENTIONAL.match(subject.strip())
    return m.group("type").lower() if m else None


def by_file_section(a, by_file: dict, user_label: str, files_now: set[str],
                    max_files: int = 40, subjects_per_file: int = 4) -> str:
    """The authorship section with each changed file's commit subjects beside it."""
    lines = [f"## Authorship by file ({user_label})"]
    if not a.user_commits:
        lines.append(f"No commits by the user found ({a.total_commits} commits, {a.authors} authors).")
        return "\n".join(lines)
    share = 100 * a.user_commits / max(1, a.total_commits)
    lines.append(f"User commits: {a.user_commits} of {a.total_commits} non-merge commits ({share:.0f}%), "
                 f"{a.authors} author(s) in total; user active {a.first} to {a.last}.")
    types = Counter(t for _, _, s in a.messages if (t := conventional_type(s)))
    if types:
        lines.append("User commits by Conventional Commits type: "
                     + ", ".join(f"{t} {n}" for t, n in sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))))
    ranked = sorted(a.files.items(), key=lambda kv: (-(kv[1]["added"] + kv[1]["deleted"]), kv[0]))
    lines.append(f"Files the user changed (top {min(len(ranked), max_files)} of {len(ranked)} by lines "
                 "changed; +added -deleted, then the user's commits to that file, newest first):")
    for p, d in ranked[:max_files]:
        gone = "" if p in files_now else "  [no longer in the repo]"
        lines.append(f"  {p}  +{d['added']} -{d['deleted']}  {d['commits']} commit(s){gone}")
        for date, sha, subject in by_file.get(p, [])[:subjects_per_file]:
            lines.append(f"    {date} {sha}  {subject[:120]}")
    return "\n".join(lines)


# ── pull requests ────────────────────────────────────────────────────────────

def prs_section(prs: list[dict], user_label: str, max_prs: int = 40, body_chars: int = 240,
                files_per_pr: int = 6) -> str:
    """The user's merged PRs, newest first. `prs` items: number, title, merged_at, additions,
    deletions, merge_commit_sha, body, files (list of paths)."""
    if not prs:
        return f"## Merged pull requests ({user_label})\n(none found)"
    lines = [f"## Merged pull requests ({user_label}; {len(prs)} merged, newest first)"]
    for pr in sorted(prs, key=lambda p: (p.get("merged_at") or "", p["number"]), reverse=True)[:max_prs]:
        sha = (pr.get("merge_commit_sha") or "")[:7]
        head = (f"  #{pr['number']} ({(pr.get('merged_at') or '')[:10]}, +{pr.get('additions', 0)} "
                f"-{pr.get('deletions', 0)}{', ' + sha if sha else ''}) {pr['title'][:140]}")
        lines.append(head)
        body = re.sub(r"<!--.*?-->", " ", pr.get("body") or "", flags=re.S)
        body = re.sub(r"\s+", " ", body).strip()
        if body:
            lines.append(f"    {body[:body_chars]}{'…' if len(body) > body_chars else ''}")
        files = pr.get("files") or []
        if files:
            more = f" (+{len(files) - files_per_pr} more)" if len(files) > files_per_pr else ""
            lines.append("    files: " + ", ".join(files[:files_per_pr]) + more)
    return "\n".join(lines)


# ── built by others ──────────────────────────────────────────────────────────

def others_section(ranked: list[tuple], index: BlameIndex, consider: int = 120, show: int = 25,
                   min_lines: int = 3) -> str:
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
