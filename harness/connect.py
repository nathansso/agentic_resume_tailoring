"""`artie connect`: GitHub sign-in and the LinkedIn URL in one step (#400).

    artie connect                          # GitHub, then your LinkedIn profile URL
    artie connect --private                # also ask for private and organisation repos
    artie connect --reauth                 # sign in to GitHub again
    artie connect --github-only            # skip LinkedIn
    artie connect --linkedin-only          # skip GitHub
    artie connect --linkedin-url <url>     # no prompt (scripts, tests)
    artie connect --json                   # one JSON document instead of text

Run it in your own terminal: GitHub's device flow needs you to open a page and type a
code. The sign-in is `harness.github_cli.sign_in`, the same flow as `artie github login`
(the token goes to the OS keychain, or `ART_GITHUB_TOKEN`; it is never printed, logged or
stored in the database). The LinkedIn URL is checked with the gateway's rule
(`harness.linkedin_url`), normalised, and saved as the active profile's `linkedin_url`
through the `update_profile` tool, so a remote store stays read-only without
`--allow-writes`. This command makes no LinkedIn request: fetching the profile is a later step.

Idempotent: with GitHub signed in and a URL saved, a second run prints the state and
changes nothing; it signs in again only for `--reauth` (or `--private`, when the token
lacks the `repo` scope) and replaces the URL only when one is given with `--linkedin-url`.

Exit status: 0 when every step asked for ended well or was skipped, 1 when one failed,
2 for bad arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Dict, Optional, Sequence

from harness import github as gh
from harness import github_cli
from harness.linkedin_url import InvalidLinkedInURL, normalize_profile_url

_PROMPT_TRIES = 3


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="artie connect", description=__doc__.splitlines()[0])
    p.add_argument("--private", action="store_true",
                   help="Also ask GitHub for the `repo` scope, so private and organisation "
                        "repos can be listed.")
    p.add_argument("--reauth", action="store_true",
                   help="Sign in to GitHub again even if you are signed in.")
    only = p.add_mutually_exclusive_group()
    only.add_argument("--github-only", action="store_true", help="Skip the LinkedIn step.")
    only.add_argument("--linkedin-only", action="store_true", help="Skip the GitHub step.")
    p.add_argument("--linkedin-url", metavar="URL",
                   help="Your LinkedIn profile URL; saves it without asking.")
    p.add_argument("--json", action="store_true", help="Print one JSON document, not text.")
    p.add_argument("--database-url", help="DB URL (default: the local SQLite store).")
    p.add_argument("--user-id", help="ART user id (default: the active profile).")
    p.add_argument("--allow-writes", action="store_true",
                   help="Let the LinkedIn URL be saved to a remote database.")
    return p


# ── the GitHub step ──────────────────────────────────────────────────────────

def _scope_words(scopes: Sequence[str]) -> str:
    private = "repo" in scopes
    return f"{', '.join(scopes) or 'no scopes reported'}; {'private and public' if private else 'public'} repos"


def _github_step(args, *, transport, sleep, clock, say) -> Dict[str, Any]:
    state: Dict[str, Any] = gh.login_status()

    def have(note=None) -> Dict[str, Any]:
        return {"connected": True, "action": "already", "login": state["login"],
                "scopes": state["scopes"], "private_scope": state["private_scope"],
                "source": state["source"], "note": note}

    if state["logged_in"]:
        wants_private = args.private and not state["private_scope"]
        if not args.reauth and not wants_private:
            return have()
        if state["source"] == "env":
            # The environment token wins over the keychain, so a new sign-in could not take effect.
            return have(f"The token comes from {gh.TOKEN_ENV}, which a new sign-in cannot "
                        "replace. Unset it, or set a token with the scopes you want.")
    elif state.get("error") and state["error"]["code"] != "bad_token":
        # A token is stored but GitHub could not be asked about it (offline, rate limit):
        # not a reason to start a new sign-in.
        return {"connected": False, "action": "failed", "login": None, "scopes": [],
                "private_scope": False, "source": state["source"], "error": state["error"]}
    done = github_cli.sign_in(args.private, transport=transport, sleep=sleep, clock=clock,
                              say=say)
    if not done["ok"]:
        # A failed re-sign-in leaves the earlier token as it was.
        return {"connected": state["logged_in"], "action": "failed", "login": state["login"],
                "scopes": state["scopes"], "private_scope": state["private_scope"],
                "source": state["source"], "error": done["error"]}
    return {"connected": True, "action": "signed_in", "login": done["login"],
            "scopes": done["scopes"], "private_scope": done["private_scope"],
            "source": "keychain", "note": None}


# ── the LinkedIn step ────────────────────────────────────────────────────────

def _read_url(raw: str) -> Optional[str]:
    try:
        return normalize_profile_url(raw)
    except InvalidLinkedInURL:
        return None


def _save_url(user_id, url: str, allow_writes: bool) -> Optional[Dict[str, str]]:
    from harness.contract import invoke
    out = invoke("update_profile", user_id, {"fields": {"linkedin_url": url}},
                 allow_writes=allow_writes)
    error = out.get("error")
    if not error:
        return None
    message = error["message"]
    if error["code"] == "read_only":
        message = ("This store is read-only (a remote database), so the LinkedIn URL was not "
                   "saved. Add --allow-writes to write to it, or use the local store.")
    return {"code": error["code"], "message": message}


def _linkedin_step(args, *, user_id, allow_writes, interactive, ask) -> Dict[str, Any]:
    from harness.contract import invoke
    profile = invoke("get_profile", user_id, {})
    if profile.get("error"):
        return {"connected": False, "action": "failed", "url": None, "error": profile["error"]}
    current = (profile.get("linkedin_url") or "").strip() or None

    url, how = None, None
    if args.linkedin_url is not None:
        url = _read_url(args.linkedin_url)
        if url is None:
            return {"connected": bool(current), "action": "failed", "url": current,
                    "error": {"code": "invalid_url",
                              "message": "Not a LinkedIn profile URL. "
                                         "Give one of the form https://www.linkedin.com/in/<name>."}}
    elif current:
        return {"connected": True, "action": "already", "url": current}
    elif interactive:
        for _ in range(_PROMPT_TRIES):
            answer = ask("Your LinkedIn profile URL (linkedin.com/in/<name>), "
                         "or press Enter to skip: ").strip()
            if not answer:
                break
            url = _read_url(answer)
            if url:
                break
            print("  That is not a LinkedIn profile URL. Use the form "
                  "https://www.linkedin.com/in/<name>.", file=sys.stderr)
        else:
            return {"connected": False, "action": "failed", "url": None,
                    "error": {"code": "invalid_url", "message": "No valid LinkedIn URL was given."}}
    if url is None:
        return {"connected": False, "action": "skipped", "url": None,
                "note": None if interactive else
                "Not asked: there is no terminal to ask in. Add --linkedin-url <url>."}
    if current and _read_url(current) == url:
        return {"connected": True, "action": "unchanged", "url": current}
    error = _save_url(user_id, url, allow_writes)
    if error:
        return {"connected": bool(current), "action": "failed", "url": current, "error": error}
    return {"connected": True, "action": "saved", "url": url, "replaced": current}


# ── what to do next ──────────────────────────────────────────────────────────

def next_step(github_connected: bool, linkedin_url: Optional[str],
              ingest_linkedin: Optional[bool] = None) -> str:
    """One sentence on what to do now. `artie ingest` is not promised: it is #398, and the
    agent's LinkedIn tool (`ingest_linkedin`, #355) is named only once the contract has it."""
    if ingest_linkedin is None:
        from harness.contract import BY_NAME
        ingest_linkedin = "ingest_linkedin" in BY_NAME
    if ingest_linkedin and (github_connected or linkedin_url):
        return "Ask your agent to \"pull my LinkedIn and GitHub\"."
    if github_connected:
        text = ("Ask your agent to read your GitHub repos, or run /artie:setup in Claude Code "
                "($artie-setup in Codex) to build your graph.")
        if linkedin_url:
            text += (" Your LinkedIn URL is saved; pulling the profile in is not available yet "
                     "(`artie ingest`, coming), so give your agent your LinkedIn data export "
                     "meanwhile.")
        return text
    if linkedin_url:
        return ("Run /artie:setup in Claude Code ($artie-setup in Codex) to build your graph. "
                "Your LinkedIn URL is saved; pulling the profile in is not available yet "
                "(`artie ingest`, coming). Run `artie connect` again to add GitHub.")
    return "Nothing is connected yet. Run `artie connect` again."


# ── the command ──────────────────────────────────────────────────────────────

def _lines(result: Dict[str, Any]) -> list:
    out = []
    gh_r, li_r = result.get("github"), result.get("linkedin")
    if gh_r:
        who = gh_r.get("login")
        if gh_r["action"] == "signed_in":
            out.append(f"Signed in to GitHub as {who}.")
        elif gh_r["action"] == "already":
            out.append(f"Already signed in to GitHub as {who}; nothing changed "
                       "(artie connect --reauth signs in again).")
        else:
            out.append(f"GitHub sign-in failed ({gh_r['error']['code']}): {gh_r['error']['message']}")
        if gh_r.get("note"):
            out.append(gh_r["note"])
    if li_r:
        action = li_r["action"]
        if action == "saved":
            out.append(f"Saved your LinkedIn URL: {li_r['url']}")
        elif action in ("already", "unchanged"):
            out.append(f"Your LinkedIn URL is {li_r['url']}; nothing changed "
                       "(artie connect --linkedin-url <url> replaces it).")
        elif action == "skipped":
            out.append("LinkedIn skipped." + (f" {li_r['note']}" if li_r.get("note") else ""))
        else:
            out.append(f"LinkedIn not saved: {li_r['error']['message']}")
    out.append("")
    out.append("Connected:")
    if gh_r:
        out.append("  GitHub    " + (f"{gh_r['login']} ({_scope_words(gh_r['scopes'])})"
                                     if gh_r["connected"] else "not signed in"))
    if li_r:
        out.append("  LinkedIn  " + (li_r["url"] if li_r["connected"] and li_r.get("url")
                                     else "no URL saved"))
    out.append("")
    out.append("Next: " + result["next"])
    return out


def run(args, *, user_id=None, allow_writes: bool = True, interactive: bool = False,
        transport=None, sleep=None, clock=None, say: Optional[Callable[[str], None]] = None,
        ask: Optional[Callable[[str], str]] = None, write=None) -> int:
    """Execute against an already-configured store. Tests call this directly."""
    import time
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    say = say or (lambda line: print(line, file=sys.stderr, flush=True))
    ask = ask or input
    write = write or (lambda text: sys.stdout.write(text + "\n"))

    result: Dict[str, Any] = {"ok": True}
    if not args.linkedin_only:
        result["github"] = _github_step(args, transport=transport, sleep=sleep, clock=clock,
                                        say=say)
    if not args.github_only:
        result["linkedin"] = _linkedin_step(args, user_id=user_id, allow_writes=allow_writes,
                                            interactive=interactive, ask=ask)
    result["ok"] = all(result[k]["action"] != "failed" for k in ("github", "linkedin") if k in result)
    result["next"] = next_step(bool(result.get("github", {}).get("connected")),
                               result.get("linkedin", {}).get("url"))
    if args.json:
        write(json.dumps(result, sort_keys=True, ensure_ascii=False))
    else:
        for line in _lines(result):
            write(line)
    return 0 if result["ok"] else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(list(argv or []))
    if args.linkedin_url is not None and args.github_only:
        _parser().error("--linkedin-url cannot be used with --github-only")
    user_id, writes = None, True
    if not args.github_only:
        from harness.runtime import bootstrap
        user_id, writes = bootstrap(args.database_url, args.user_id, args.allow_writes,
                                    prepare=True)
    return run(args, user_id=user_id, allow_writes=writes,
               interactive=bool(sys.stdin and sys.stdin.isatty()))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
