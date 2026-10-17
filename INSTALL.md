# Installing ART

ART is a package your coding agent drives: the Claude Code plugin, the Codex integration and
the `art-mcp` package are below, with the editor, GitHub sign-in, backup and troubleshooting.
The design is in [`docs/harness.md`](docs/harness.md). The public site is
https://artie-sand.vercel.app; this file is the full reference.

## The Claude Code plugin (#201)

The simplest way to use ART is the plugin. It registers the MCP server, the `/art:*`
commands and the hooks:

```
/plugin marketplace add nathansso/agentic_resume_tailoring
/plugin install art@art
```

It needs `uv`, which `/art:setup` installs if it is missing (below). For PDFs, ART downloads a pinned Tectonic on first use (set `ART_NO_DOWNLOAD=1`
to opt out). See [`plugin/README.md`](plugin/README.md).

**No uv yet? Run `/art:setup` (#385).** When ART's tools don't answer, the setup checks for
`uv` (on PATH, or in `~/.local/bin`, `%USERPROFILE%\.local\bin` on Windows) and, with your
yes to each command, runs uv's official installer and installs ART as a command:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh                                  # macOS, Linux
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"   # Windows
uv tool install --python 3.12 "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.1.2/art_mcp-0.1.2-py3-none-any.whl"
uv tool update-shell
```

`art` is then a real command. The plugin's server and hooks run `uvx --python 3.12`, the
same Python as that install, so `uvx` starts from it without resolving it again, and a
newer Python uv picks up later can't change that. Restart ART once: `/mcp` and reconnect `art` in the Claude Code terminal,
quit and reopen the desktop app, or restart Codex. Then run `/art:setup` again; it carries on
with your sources. Codex runs the same steps through the `$art-setup` skill.

## Codex (#203)

The same plugin works in Codex:

```bash
codex plugin marketplace add nathansso/agentic_resume_tailoring
codex plugin add art@art
```

Codex 0.131 and later load a plugin's hooks, so that is the whole install, plus trusting
the hooks with `/hooks` in Codex (it skips a plugin's hooks until you do). For an older
Codex, or Codex without the plugin, install them into your own `hooks.json` instead:

```bash
uvx --python 3.12 --from "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.1.2/art_mcp-0.1.2-py3-none-any.whl" art hooks codex --write
```

With the plugin installed that command adds nothing and removes ART's own entries from
`hooks.json`, so no hook runs twice. Without it, running it again after an upgrade replaces
ART's hooks from an earlier install (any `uvx --from` git or wheel source) and leaves your own
alone. See [`integrations/codex/README.md`](integrations/codex/README.md).

## The harness: `art-mcp` (#194)

The package is `art-mcp`. Its default install is model-free and light (no torch, no LLM
clients); extras add `[embed]` (sentence-transformers), `[pdf]` (docling, pypdf,
python-docx), `[ui]` (`art ui`, the local editor) and `[postgres]`. It is not on PyPI yet.
Each release attaches its wheel, with the editor built in, to a GitHub Release, and the
plugin pins that exact URL (`v<version>` is `ART_VERSION` in `harness/__init__.py`):

```bash
# Register the MCP server with Claude Code
claude mcp add art -- uvx --python 3.12 --from "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.1.2/art_mcp-0.1.2-py3-none-any.whl" art mcp

# The same tools as a JSON CLI
uvx --python 3.12 --from "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.1.2/art_mcp-0.1.2-py3-none-any.whl" art --list
```

`art mcp` and `art-mcp` run the same server; the plugin uses `art mcp` so it needs only the
`art` entry point (a machine's Application Control policy may block one executable and not
the other).

On first start it creates `~/.art/art.db` (or `$ART_DATA_DIR/art.db`) and a default
profile.

### The editor: `art ui` (#255)

The release wheel has the built editor in it, so no checkout, Node or `npm` is needed:

```bash
uvx --python 3.12 --from "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.1.2/art_mcp-0.1.2-py3-none-any.whl" art ui --job <job_id>
```

It opens http://127.0.0.1:8765/. The plugin's `/art:open` prints this command for the
job you name. It needs the `[ui]` extra, which the pinned spec already has. An install
straight from the repository (`uvx --from git+https://github.com/nathansso/agentic_resume_tailoring ...`)
has no editor, because `web/static` is not committed; `art ui` says so and prints the
command above.

To build the editor yourself, see [Building from source](#building-from-source).

### Building from source

From a checkout, build the editor, then run it from source or from a wheel you build:

```bash
git clone https://github.com/nathansso/agentic_resume_tailoring.git
cd agentic_resume_tailoring
npm --prefix web/frontend ci && npm --prefix web/frontend run build
python -m web.local_ui                     # from source
uv build                                   # dist/art_mcp-<version>-py3-none-any.whl
uvx --from "dist/art_mcp-0.1.2-py3-none-any.whl[ui]" art ui
```

CI builds that wheel, installs it with no extras into an empty venv, and drives `art-mcp`
over stdio (`scripts/smoke_art_mcp.py`). To register a checkout's server with Claude Code
instead of the release wheel, see [`docs/harness.md`](docs/harness.md) § 15.

### Offline starts, and Windows

uvx downloads the pinned wheel once and keeps it, and the environment it built, in uv's
cache. The hooks run `uvx --offline` and end in `; exit 0`: they use only what the MCP
server's start cached, never wait on the network, and exit 0 and say nothing when uvx or
the cache is missing, so a hook can't block your message.

The MCP server start is different: uvx resolves its requirements on every start, and uv
re-checks a cached answer once it is older than a few minutes (the wheel's URL, and PyPI's
index for the dependencies). With the network on that costs a second or two. With it off,
a start after the cache has aged fails once uv gives up retrying (about 15 s). Two fixes,
both from the same cache:

- `UV_OFFLINE=1` in the environment Claude Code or Codex starts from. uv then never
  touches the network and uses the cache as it is (after one earlier online start).
- `uv tool install --python 3.12 "art-mcp[ui] @ <the pinned URL>"` once. uvx runs an installed tool
  without re-resolving it. Upgrade it by installing the next pin.

On Windows, a deep default uv cache path can break the install (long paths). Point uv at a short
one before starting Claude Code or Codex, in PowerShell:

```powershell
[Environment]::SetEnvironmentVariable("UV_CACHE_DIR", "C:\uvc", "User")
```

`UV_CACHE_DIR` takes effect in newly started programs. If Application Control blocks
`art-mcp.exe`, the plugin is unaffected, since it runs `art mcp`.

### Jev (optional): strengthen the checks

ART works fully without Jev, so you can skip this section. If you have a TypeSafe API key, ART can
use it, from your machine with no ART server in between, to strengthen some checks by asking
[TypeSafe's Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) a few narrow classification questions that plain rules answer less well: does a
revised bullet say more than its cited evidence, does a bullet reword a topic you banned, does a
bullet show a requirement, is a message a standing preference, which of your saved bullets or tracks
fits a job. Jev never writes resume text. With no key, every decision point runs its own
deterministic fallback (a check of the bullet's wording against its cited source, the literal term
match, word overlap, the host asking you), and the hard gates still run on those rules; Jev adds
the reworded cases they miss.

**To turn it on, set the key** in one of these places, checked in this order:

```bash
# 1. The environment ART is started from (the usual way: Claude Code and Codex start the
#    MCP server, so it must be in their environment, e.g. your shell profile)
export TYPESAFE_API_KEY='...'

# 2. A checkout only: .env at the repo root
TYPESAFE_API_KEY=...

# 3. The OS keychain, through the optional keyring package. ART looks up service "art-mcp",
#    user "typesafe-api-key". Install keyring next to ART (uvx --with keyring ...) and store it once:
uvx --from keyring keyring set art-mcp typesafe-api-key
```

The plugin's `uvx` command does not include `keyring`, so use the environment variable there. The
key is never logged, stored in the database or put in an error message. `art jev status` shows
whether ART can see one (`"key": "set"`, never the key itself), the mode, and how many decisions are
cached per point.

**What leaves your machine.** Only when a key is set and the answer is not already cached, each
request carries a narrow state and its questions, to `api.typesafe.ai`:

| Decision | Sent |
|---|---|
| Support check | a changed bullet, the bullet it revises, the cited source text |
| Reworded negative pin | a changed bullet or item field, and the topic you banned |
| Requirement coverage | one bullet or education entry, and the posting's requirement texts |
| Memory gate | your message; for one that refers back ("never list that again"), the last 600 characters of the previous reply too; the names of your skills, roles and projects |
| Bullet choice and track baseline | the job title and its top requirements, and the candidate bullets or track names |

Your whole resume and the whole posting are not sent. Check TypeSafe's terms for how it handles
what it receives.

**The privacy switch.** `ART_JEV_MODE` is `auto` (the default: the local cache, then Jev if a key
is set, then the fallback), `off` (never the cache or the network; always the fallback) or `replay`
(the cache only; a miss is an error, for tests). An unrecognized value means `off`. To be sure
nothing is sent, leave the key unset or set `ART_JEV_MODE=off`.

**Recordings.** Answers are cached in your local database, so a repeated question costs nothing.
`art jev` moves them between machines or into a test:

```bash
art jev status                       # mode, key set or not, cached decisions per point
art jev export decisions.json        # every cached decision (questions and answers, never the state)
art jev import decisions.json        # merge them in; --overwrite replaces rows already there
```

A cached question can name text (the bullet choice lists your approved bullets, the memory gate your
item names, a coverage question the posting's requirements), so treat an export from your own store
as personal data; `art jev export` prints a notice to that effect on stderr. See
[`docs/harness.md`](docs/harness.md) § 4 and [`docs/architecture.md`](docs/architecture.md) § 8.3.

### Backup and migration (#195)

```bash
art export --out ~/backups/art.zip                       # the profile + applications/, no secrets
art import ~/backups/art.zip                             # into a store with no data for that user
art import ~/backups/art.zip --merge                     # keep what is there, add the rest
art import ~/backups/art.zip --replace --confirm-replace # delete that user's data first
```

A one-time migration from the hosted web app reads its Postgres database directly (needs
the `[postgres]` extra, e.g. `uvx --from 'art-mcp[postgres]' art ...`):

```bash
export ART_IMPORT_SOURCE_URL='postgresql://<user>:<password>@<host>:5432/postgres'
art import --from-supabase --user-id <profile-id> --dry-run
art import --from-supabase --user-id <profile-id> --set-active
```

`<profile-id>` is the `user_id` of the account's row in the web app's `user` table (not
the Supabase Auth uid); `SELECT user_id, email FROM "user"` finds it. The connection
string is the project's Postgres URL from the Supabase dashboard, for a role that can read
`public` (the `postgres` role does; a restricted role sees no rows through row-level
security, and the import says so rather than importing nothing).

The source is never `DATABASE_URL`, is opened read-only, and is never written to. The
destination is always local SQLite (`$ART_DATA_DIR/art.db`). Use the id of the real
account's profile, not the local fallback one (`user@example.com`), which import refuses.

### GitHub repos (optional, #353)

`art github login` signs you in with GitHub's device flow, so ART can list your repos
(`list_github_repos`) and your agent can pick which to read. You open a page and type a code;
no password or secret passes through ART. Run it in your own terminal:

```bash
art github login               # public repos (read:user)
art github login --private     # also private repos (adds the repo scope)
art github status              # who is signed in; never prints the token
art github logout              # removes the stored token
```

`/art:setup` puts `art` on your PATH (`uv tool install`, above). If you have not run it, the
plugin runs ART through `uvx`, which puts no `art` on your PATH, so run the same commands
through `uvx`:

```bash
uvx --python 3.12 --from "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.1.2/art_mcp-0.1.2-py3-none-any.whl" art github login
uvx --python 3.12 --from "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.1.2/art_mcp-0.1.2-py3-none-any.whl" art github login --private
```

The token goes to the OS keychain through the `keyring` package (service `art-mcp`,
user `github-token`), else it is read from `ART_GITHUB_TOKEN`, which also takes a personal
access token. It is never stored in the database, never in an `art export` bundle, and never
logged. `keyring` ships with ART from v0.1.1; on a machine with no usable
keychain backend (some headless Linux setups), `art github login` says so before it starts,
and `ART_GITHUB_TOKEN` is the way in. Nothing about your repos is sent anywhere but your
own agent: ART reads them from `api.github.com` with your token and hands the result to it.

**Maintainer note: register the GitHub OAuth App (once).** `art github login` needs a client ID
and no client secret.

1. GitHub, Settings, Developer settings, OAuth Apps, New OAuth App. Name it `ARTie`; the
   homepage URL is https://artie-sand.vercel.app. The authorization callback URL is required
   by the form and unused by the device flow (any URL, for example the homepage).
2. On the App's page, tick **Enable Device Flow**, and save.
3. Copy its **Client ID** (it is public; do not generate or keep a client secret for this).
4. Put it in `DEFAULT_CLIENT_ID` in `harness/github.py` and ship a release. Until then
   `ART_GITHUB_CLIENT_ID=<client id>` in the environment makes `art github login` work, and
   with neither set it fails naming this step.
