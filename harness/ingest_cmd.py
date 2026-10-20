"""`artie ingest`: read the user's sources into dossiers for their agent (#398).

    artie ingest                         # every repo you own on GitHub (forks left out)
    artie ingest --repo ada/forecast     # just these (owner/name or name; repeatable)
    artie ingest --path ~/code/thesis    # a repository folder on this machine (repeatable)
    artie ingest --json                  # one JSON document on stdout

The deterministic half of ingestion; the user runs it, not the agent. For each repository it
keeps a bare clone under `<data dir>/repos/` (fetched through `harness/github.py`, the only
network client here), builds #397's `blame-20k` dossier at the default branch's commit
(`harness/dossier/`), and stages it in the store (`harness/sources.py`). It is incremental by
commit: a repository whose commit is unchanged is not rebuilt, and its staged row is left as
it was. It ends by telling the user to ask their agent to "process my new sources"; the agent
then reads each dossier with `list_staged_sources`, writes records with `upsert_items`, and
marks it processed.

**Steps.** The command runs `STEPS` in order, each one source family. GitHub repos (and
`--path` folders) are the only step today. The LinkedIn half (#355's gateway client and its
shared mapping, then #396) joins as one more `Step` in that list: it takes the same context
and returns the same per-source results, so the command's output and exit status cover it.

Who the user is in each history comes from every email on their GitHub account, the emails
GitHub links to their login on the repo's commits, the machine's `git config` identity, their
display name and login, and the repo's `.mailmap` (`harness/dossier/identity.py`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence
from uuid import UUID

PROCESS_PHRASE = "process my new sources"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="artie ingest",
                                description="Read your GitHub repos into dossiers your agent "
                                            "turns into your knowledge graph.")
    p.add_argument("--repo", action="append", default=[], metavar="OWNER/NAME",
                   help="Only this GitHub repo (repeatable). Default: every repo you own.")
    p.add_argument("--path", action="append", default=[], metavar="DIR",
                   help="A git repository folder on this machine (repeatable).")
    p.add_argument("--include-forks", action="store_true", help="Also read your forks.")
    p.add_argument("--limit", type=int, default=None, help="At most this many GitHub repos.")
    p.add_argument("--json", action="store_true", help="Print one JSON document.")
    p.add_argument("--user-id", help="ART user id (default: ~/.art pointer file)")
    p.add_argument("--database-url", help="DB URL, or 'dotenv' to use .env's DATABASE_URL")
    p.add_argument("--allow-writes", action="store_true",
                   help="Let ingest write to a remote database.")
    return p


def repos_dir() -> Path:
    data = Path(os.environ.get("ART_DATA_DIR") or Path.home() / ".art")
    return Path(os.environ.get("ART_REPOS_DIR") or data / "repos")


@dataclass
class Context:
    """What every step gets."""
    args: argparse.Namespace
    user_id: UUID
    say: Callable[[str], None]
    client: Any = None                 # a GitHubClient, or None when not signed in
    token: Optional[str] = None
    clones: Path = field(default_factory=repos_dir)
    sync: Optional[Callable[..., str]] = None   # harness.github.sync_clone, injectable


Step = Callable[[Context], List[Dict[str, Any]]]


def _result(kind: str, key: str, action: str, **extra) -> Dict[str, Any]:
    return {"kind": kind, "key": key, "action": action, **extra}


def _stage_repo(ctx: Context, kind: str, key: str, title: str, repo: Path, commit: str,
                seeds_fn: Callable[[], Any], meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    from harness import dossier, sources
    if sources.is_current(ctx.user_id, kind, key, commit, dossier.BUILDER_VERSION):
        return _result(kind, key, "unchanged", commit=commit,
                       source_id=str(sources.current(ctx.user_id, kind, key).source_id))
    seeds = seeds_fn()
    ctx.say(f"Building the dossier for {key} at {commit[:7]}…")
    got = sources.stage(ctx.user_id, kind, key, commit, dossier.BUILDER_VERSION,
                        lambda: dossier.build(repo, key, seeds, rev=commit, meta=meta),
                        title=title)
    return _result(kind, key, got["action"], commit=commit, source_id=got["source_id"])


def _account_seeds(ctx: Context):
    """Seeds from the GitHub account (one call each, once per run) and this machine."""
    from harness.dossier import Seeds, local_git_identity
    local = local_git_identity()
    if ctx.client is None:
        return local
    who = ctx.client.user()
    emails = ctx.client.account_emails()
    return local.merged(Seeds.make(emails, [who.get("name") or ""], who["login"], who.get("id")))


def github_step(ctx: Context) -> List[Dict[str, Any]]:
    """The user's own GitHub repos (or `--repo`), each cloned or fetched, then staged."""
    from harness import github as gh
    from harness.dossier import Seeds
    args = ctx.args
    if args.path and not args.repo:
        return []                                     # `--path` alone: local folders only
    if ctx.client is None:
        return [_result("github_repo", "*", "failed", error={
            "code": "not_logged_in",
            "message": "Not signed in to GitHub. Run: " + gh.login_hint()})]
    try:
        who = ctx.client.user()
        private = "repo" in who["scopes"]
        repos = ctx.client.all_repos(include_private=private)
    except gh.GitHubError as exc:
        return [_result("github_repo", "*", "failed",
                        error={"code": exc.code, "message": exc.message})]
    wanted = {r.lower() for r in args.repo}
    if wanted:
        picked = [r for r in repos if str(r.get("full_name", "")).lower() in wanted
                  or str(r.get("name", "")).lower() in wanted]
        found = {str(r["full_name"]).lower() for r in picked} | {str(r["name"]).lower() for r in picked}
        missing = sorted(w for w in wanted if w not in found)
    else:
        picked = [r for r in repos if args.include_forks or not r.get("fork")]
        missing = []
    if args.limit is not None:
        picked = picked[:max(0, args.limit)]
    out = [_result("github_repo", m, "failed", error={
        "code": "not_found", "message": f"{m} is not one of your repos on GitHub."})
        for m in missing]
    base: Dict[str, Any] = {}

    def seeds_for(full: str):
        def make():
            if "seeds" not in base:
                base["seeds"] = _account_seeds(ctx)
            linked = ctx.client.commit_emails(full, who["login"])
            return base["seeds"].merged(Seeds.make(linked))
        return make

    sync = ctx.sync or gh.sync_clone
    for r in picked:
        full = str(r["full_name"])
        owner, name = full.split("/", 1)
        dest = ctx.clones / owner / f"{name}.git"
        try:
            ctx.say(f"Fetching {full}…")
            commit = sync(gh.clone_url(full), dest, branch=r.get("default_branch"),
                          token=ctx.token)
            meta = {k: r.get(k) for k in ("description", "topics", "homepage") if r.get(k)}
            lic = (r.get("license") or {}).get("spdx_id") if isinstance(r.get("license"), dict) else None
            if lic and lic != "NOASSERTION":
                meta["license"] = lic
            out.append(_stage_repo(ctx, "github_repo", full, full, dest, commit,
                                   seeds_for(full), meta))
        except Exception as exc:  # noqa: BLE001 - one repo's failure never stops the rest
            code = getattr(exc, "code", type(exc).__name__)
            out.append(_result("github_repo", full, "failed",
                               error={"code": str(code), "message": str(getattr(exc, "message", "") or exc)[:500]}))
    return out


def local_step(ctx: Context) -> List[Dict[str, Any]]:
    """`--path` folders: built at their HEAD, nothing fetched."""
    from harness.dossier import local_git_identity
    from harness.dossier.gitread import GitError, resolve_commit
    out = []
    for raw in ctx.args.path:
        folder = Path(raw).expanduser().resolve()
        key = folder.as_posix()
        try:
            commit = resolve_commit(folder)
            out.append(_stage_repo(ctx, "local_repo", key, folder.name, folder, commit,
                                   local_git_identity if ctx.client is None
                                   else (lambda: _account_seeds(ctx)), None))
        except GitError as exc:
            out.append(_result("local_repo", key, "failed",
                               error={"code": "not_a_repo", "message": str(exc)}))
    return out


# The source families, in order. #355's LinkedIn step joins here (see the module docstring).
STEPS: List[Step] = [github_step, local_step]


def _lines(doc: Dict[str, Any]) -> List[str]:
    words = {"staged": "new", "restaged": "updated", "unchanged": "unchanged", "failed": "failed"}
    out = []
    for r in doc["results"]:
        at = f" at {r['commit'][:7]}" if r.get("commit") else ""
        line = f"  {words.get(r['action'], r['action']):<9} {r['key']}{at}"
        if r["action"] == "failed":
            line += f": {r['error']['message']}"
        out.append(line)
    c = doc["counts"]
    out.append("")
    out.append(f"{c.get('staged', 0)} source(s) waiting for your agent, "
               f"{c.get('processed', 0)} already processed.")
    out.append(doc["next"])
    return out


def run(args, *, user_id: UUID, client=None, token: Optional[str] = None,
        sync: Optional[Callable[..., str]] = None, clones: Optional[Path] = None,
        say: Optional[Callable[[str], None]] = None, write=None) -> int:
    """Execute against an already-configured store. Tests call this directly with a fake
    GitHub client and a local `sync`."""
    from harness import sources
    say = say or (lambda line: print(line, file=sys.stderr, flush=True))
    write = write or (lambda text: sys.stdout.write(text + "\n"))
    ctx = Context(args, user_id, say, client, token, clones or repos_dir(), sync)
    results: List[Dict[str, Any]] = []
    for step in STEPS:
        results.extend(step(ctx))
    counts = sources.list_staged_sources(user_id, None)["counts"]
    nxt = (f"Next: ask your agent to \"{PROCESS_PHRASE}\"." if counts.get("staged")
           else "Nothing is waiting for your agent.")
    doc = {"ok": not any(r["action"] == "failed" for r in results), "results": results,
           "counts": counts, "next": nxt}
    if args.json:
        write(json.dumps(doc, sort_keys=True, ensure_ascii=False))
    else:
        for line in _lines(doc):
            write(line)
    return 0 if doc["ok"] else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(list(argv or []))
    from harness.runtime import bootstrap
    user_id, writes = bootstrap(args.database_url, args.user_id, args.allow_writes, prepare=True)
    if user_id is None:
        print("artie ingest: no ART profile. Run /artie:setup first.", file=sys.stderr)
        return 2
    if not writes:
        print("artie ingest: this store is read-only (a remote database without "
              "--allow-writes).", file=sys.stderr)
        return 2
    from harness import github as gh
    token = gh.resolve_token()
    client = gh.GitHubClient(token) if token else None
    return run(args, user_id=user_id, client=client, token=token)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
