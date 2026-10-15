"""The Claude Code plugin (issue #201): manifests, skills, commands and hooks.

The plugin is text a model reads, so the tests hold it to the code: every tool
a skill or command tells the model to call exists in the contract, and none of
the tools still to come (#199) is taught early. The hooks are tested as
functions and through `art hook` with a real stdin payload.
"""

import io
import json
import re
from pathlib import Path

import pytest

from harness.contract import BY_NAME

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"
TEXTS = sorted([*PLUGIN.glob("skills/*/SKILL.md"), *PLUGIN.glob("commands/*.md")])
# Tools docs/harness.md § 9 plans but the code does not expose yet.
NOT_YET = {"record_feedback", "check_draft"}


def _json(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _frontmatter(path):
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")   # CRLF checkouts
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, f"{path.name} has no frontmatter"
    return dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line), text


def test_manifests_point_at_the_plugin_and_its_server():
    assert _json("plugin/.claude-plugin/plugin.json")["name"] == "art"
    market = _json(".claude-plugin/marketplace.json")
    assert [p["source"] for p in market["plugins"]] == ["./plugin"]
    server = _json("plugin/.mcp.json")["mcpServers"]["art"]
    # The plugin runs through the `art` entry point alone (#256).
    assert server["command"] == "uvx" and server["args"][-2:] == ["art", "mcp"]


def test_hooks_run_art_hook_events_from_the_same_package_as_the_server():
    from harness.hooks import EVENTS

    hooks = _json("plugin/hooks/hooks.json")["hooks"]
    source = _json("plugin/.mcp.json")["mcpServers"]["art"]["args"][1]
    from harness.hooks import hook_command
    wiring = {"UserPromptSubmit": ("user-prompt", None), "SessionStart": ("session-start", "compact")}
    assert set(hooks) == set(wiring)
    for event, (arg, matcher) in wiring.items():
        [group] = hooks[event]
        assert group.get("matcher") == matcher
        [hook] = group["hooks"]
        assert hook["command"] == hook_command(arg, source)
        assert EVENTS[arg][0] == event


def test_every_skill_and_command_has_frontmatter():
    for path in TEXTS:
        meta, _ = _frontmatter(path)
        assert meta.get("description", "").strip(), path
        if path.name == "SKILL.md":
            assert meta["name"].strip() == path.parent.name


def test_the_plugin_teaches_only_tools_that_exist():
    called = set()
    for path in TEXTS:
        _, text = _frontmatter(path)
        called |= set(re.findall(r"`([a-z_]+)(?:\(|`)", text))
    tools = {t for t in called if t in BY_NAME or t in NOT_YET}
    assert not tools & NOT_YET, tools & NOT_YET
    assert {"art_briefing", "open_job", "execute_plan", "patch_plan", "render",
            "upsert_items", "update_profile", "checkout", "history", "suggest_actions",
            "promote_bullet", "approve_variant", "save_baseline"} <= tools


def test_the_commands_are_the_issues_set_less_the_deferred_library():
    names = {p.stem for p in PLUGIN.glob("commands/*.md")}
    assert names == {"tailor", "setup", "open", "history", "revert", "prefs"}


# ── hooks ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def job(kg, isolated_engine):
    from sqlmodel import Session, select

    from database.models import UserJobResult
    from harness import tree

    with Session(isolated_engine) as s:
        job_id = s.exec(select(UserJobResult).where(UserJobResult.user_id == kg)).first().job_id
    tree.commit_node(kg, job_id, content={"experiences": [
        {"title": "Data Science Intern", "company": "IDX Exchange", "bullets": ["Old bullet"]}]},
        source="host")
    return kg, job_id


def _edit(uid, job_id, bullets, source="editor"):
    from harness import tree
    return tree.commit_node(uid, job_id, source=source, content={"experiences": [
        {"title": "Data Science Intern", "company": "IDX Exchange", "bullets": bullets}]})


def test_the_prompt_hook_reports_only_new_editor_edits(job):
    from harness.hooks import user_prompt

    uid, job_id = job
    payload = {"session_id": "s1", "prompt": "hi"}
    assert user_prompt(uid, payload) is None            # first prompt: set the cursor only
    assert user_prompt(uid, payload) is None            # nothing new

    node = _edit(uid, job_id, ["New bullet"])
    text = user_prompt(uid, payload)
    assert f"job {job_id}" in text and node["node_id"] in text
    assert 'added "New bullet"' in text and 'removed "Old bullet"' in text
    assert "do not overwrite" in text
    assert user_prompt(uid, payload) is None            # reported once

    _edit(uid, job_id, ["Host bullet"], source="host")  # a host commit is not news
    assert user_prompt(uid, payload) is None
    assert user_prompt(uid, {"session_id": "s2"}) is None   # a new session starts at "now"


def test_the_prompt_hook_names_the_bullets_a_tex_edit_changed(job):
    """Several saves in a row read as one change, from the last version that was not an edit (#278)."""
    from harness import texdiff, tree
    from harness.hooks import user_prompt

    uid, job_id = job
    payload = {"session_id": "s1", "prompt": "hi"}
    assert user_prompt(uid, payload) is None
    head = tree.get_head(uid, job_id)["head"]
    tex = texdiff.node_tex(uid, head)
    first = tree.commit_node(uid, job_id, content=head["content"], source="editor",
                             edited_tex=tex.replace("Old bullet", "Older bullet"))
    tree.commit_node(uid, job_id, content=head["content"], source="editor",
                     edited_tex=first["edited_tex"].replace(
                         "\\begin{center}", "\\begin{center}\n    \\small Phone 555-0100", 1))

    text = user_prompt(uid, payload)
    assert '"Old bullet" became "Older bullet"' in text                    # the earlier save counts too
    assert "unmapped change in header" in text and "+\\small Phone 555-0100" in text
    assert "discards_editor_edits" in text and text.count(f"job {job_id}") == 1


def test_the_compact_hook_restores_pins_verbatim_and_the_current_job(job):
    from harness.hooks import session_start, user_prompt

    uid, job_id = job
    user_prompt(uid, {"session_id": "s1"})
    _edit(uid, job_id, ["Edited"])
    user_prompt(uid, {"session_id": "s1"})
    text = session_start(uid, {"session_id": "s1", "source": "compact"})
    assert "- Never mention coursework projects" in text        # the kg fixture's pin
    assert f"job {job_id}" in text
    assert session_start(uid, {"session_id": "s1", "source": "startup"}) is None


def test_art_hook_speaks_claude_codes_json_and_never_fails(job, monkeypatch, capsys):
    from harness import hooks

    uid, job_id = job
    monkeypatch.setattr("harness.runtime.bootstrap", lambda *a, **k: (uid, True))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"session_id": "s9", "source": "compact", "hook_event_name": "SessionStart"})))
    assert hooks.main(["session-start"]) == 0
    reply = json.loads(capsys.readouterr().out)
    assert reply["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Never mention coursework projects" in reply["hookSpecificOutput"]["additionalContext"]

    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert hooks.main(["user-prompt"]) == 0
    assert capsys.readouterr().out == ""
    assert hooks.run("user-prompt", {}, None) is None             # no bound user: silent


# ── Codex (#203) ─────────────────────────────────────────────────────────────

def test_codex_shares_the_plugin_directory_and_its_skills():
    codex = _json("plugin/.codex-plugin/plugin.json")
    claude = _json("plugin/.claude-plugin/plugin.json")
    assert codex["name"] == claude["name"] == "art" and codex["version"] == claude["version"]
    assert (PLUGIN / codex["skills"]).resolve() == (PLUGIN / "skills").resolve()
    assert (PLUGIN / codex["mcpServers"]).is_file()
    [entry] = _json(".agents/plugins/marketplace.json")["plugins"]
    assert entry["name"] == "art" and entry["source"] == {"source": "local", "path": "./plugin"}


def test_one_hook_config_serves_both_hosts():
    from harness.hooks import SOURCE, hook_config

    assert _json("plugin/hooks/hooks.json") == hook_config()
    assert SOURCE == _json("plugin/.mcp.json")["mcpServers"]["art"]["args"][1]


def test_skills_name_no_host_specific_slash_command():
    for path in PLUGIN.glob("skills/*/SKILL.md"):
        assert "/art:" not in path.read_text(encoding="utf-8"), path


def test_art_hooks_codex_merges_without_touching_existing_hooks(tmp_path, monkeypatch, capsys):
    from harness import entry
    from harness.hooks import hook_config

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    mine = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "my-lint"}]}],
                      "Stop": [{"hooks": [{"type": "command", "command": "notify"}]}]}}
    (tmp_path / "hooks.json").write_text(json.dumps(mine), encoding="utf-8")

    assert entry.main(["hooks", "codex", "--write", "--from", "art-mcp"]) == 0
    got = json.loads((tmp_path / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    ours = hook_config("art-mcp")["hooks"]
    assert got["Stop"] == mine["hooks"]["Stop"]
    assert got["UserPromptSubmit"] == mine["hooks"]["UserPromptSubmit"] + ours["UserPromptSubmit"]
    assert got["SessionStart"] == ours["SessionStart"]
    assert "/hooks" in capsys.readouterr().err

    first = (tmp_path / "hooks.json").read_bytes()
    entry.main(["hooks", "codex", "--write", "--from", "art-mcp"])    # idempotent
    assert (tmp_path / "hooks.json").read_bytes() == first

    assert entry.main(["hooks", "codex"]) == 0                       # print only
    assert json.loads(capsys.readouterr().out) == hook_config()


_OLD_GIT = "git+https://github.com/nathansso/agentic_resume_tailoring"
_OLD_WHEEL = ("art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/"
              "download/v0.0.9/art_mcp-0.0.9-py3-none-any.whl")


def _old_hook(source, arg, tail=""):
    return {"type": "command", "timeout": 30, "command": f'uvx --from "{source}" art hook {arg}{tail}'}


def _codex_config(tmp_path, monkeypatch, config):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


def test_art_hooks_codex_replaces_hooks_from_earlier_installs(tmp_path, monkeypatch, capsys):
    """#283: the git+ source (with and without a ref), an older release wheel and the pre-#256
    line shape all go; only the pinned entries stay."""
    from harness import entry
    from harness.hooks import hook_config

    old = {"hooks": {
        "UserPromptSubmit": [
            {"hooks": [_old_hook(_OLD_GIT, "user-prompt")]},
            {"hooks": [_old_hook(_OLD_GIT + "@main", "user-prompt", "; exit 0")]},
            {"hooks": [_old_hook("art-mcp[ui] @ " + _OLD_GIT + ".git@v0.0.1", "user-prompt")]},
        ],
        "SessionStart": [
            {"matcher": "compact", "hooks": [_old_hook(_OLD_WHEEL, "session-start", "; exit 0")]},
            {"matcher": "compact",
             "hooks": [_old_hook(_OLD_GIT, "session-start", "; exit 0")]},
        ]}}
    path = _codex_config(tmp_path, monkeypatch, old)

    assert entry.main(["hooks", "codex", "--write"]) == 0
    got = json.loads(path.read_text(encoding="utf-8"))
    assert got == hook_config()                         # only the pinned entries remain
    commands = [h["command"] for gs in got["hooks"].values() for g in gs for h in g["hooks"]]
    assert len(commands) == 2 and all("git+" not in c and "0.0.9" not in c for c in commands)
    err = capsys.readouterr().err
    assert err.count("replaced") >= 5 and "git+https" in err and "/hooks" in err


def test_art_hooks_codex_keeps_other_hooks_and_their_order(tmp_path, monkeypatch):
    from harness import entry
    from harness.hooks import hook_config

    lint = {"type": "command", "command": "my-lint"}
    other_uvx = {"type": "command",
                 "command": 'uvx --from "git+https://github.com/acme/other" tool hook user-prompt'}
    other_mcp = {"type": "command", "command": 'uvx --from "art-mcp" art-mcp --serve'}
    notify = {"type": "command", "command": "notify"}
    config = {"description": "mine", "hooks": {
        "UserPromptSubmit": [{"hooks": [lint]}, {"hooks": [_old_hook(_OLD_GIT, "user-prompt")]},
                             {"hooks": [other_uvx]}, {"hooks": [other_mcp]}],
        "SessionStart": [{"matcher": "compact", "hooks": [notify, _old_hook(_OLD_GIT, "session-start")]}],
        "Stop": [{"hooks": [notify]}]}}
    path = _codex_config(tmp_path, monkeypatch, config)

    assert entry.main(["hooks", "codex", "--write"]) == 0
    got = json.loads(path.read_text(encoding="utf-8"))
    ours = hook_config()["hooks"]
    assert got["description"] == "mine" and got["hooks"]["Stop"] == [{"hooks": [notify]}]
    # The user's groups keep their order; ours follow them.
    assert got["hooks"]["UserPromptSubmit"] == (
        [{"hooks": [lint]}, {"hooks": [other_uvx]}, {"hooks": [other_mcp]}] + ours["UserPromptSubmit"])
    # A group shared with the user's handler loses only ART's old handler.
    assert got["hooks"]["SessionStart"] == (
        [{"matcher": "compact", "hooks": [notify]}] + ours["SessionStart"])


def test_art_hooks_codex_second_write_changes_nothing(tmp_path, monkeypatch, capsys):
    from harness import entry

    path = _codex_config(tmp_path, monkeypatch, {"hooks": {
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "my-lint"},
                                        _old_hook(_OLD_GIT, "user-prompt")]}]}})
    assert entry.main(["hooks", "codex", "--write"]) == 0
    first = path.read_bytes()
    mtime = path.stat().st_mtime_ns
    capsys.readouterr()

    assert entry.main(["hooks", "codex", "--write"]) == 0
    assert path.read_bytes() == first
    assert path.stat().st_mtime_ns == mtime             # not even rewritten
    assert "nothing changed" in capsys.readouterr().err


def test_art_hooks_codex_drops_a_duplicate_current_entry(tmp_path, monkeypatch):
    from harness import entry
    from harness.hooks import hook_config

    ours = hook_config()["hooks"]
    path = _codex_config(tmp_path, monkeypatch, {"hooks": {
        "UserPromptSubmit": ours["UserPromptSubmit"] * 2, "SessionStart": ours["SessionStart"]}})
    assert entry.main(["hooks", "codex", "--write"]) == 0
    assert json.loads(path.read_text(encoding="utf-8")) == hook_config()


def test_is_art_hook_recognises_any_source_and_only_art():
    from harness.hooks import hook_command, is_art_hook

    assert is_art_hook(hook_command("user-prompt"))
    assert is_art_hook(hook_command("session-start", _OLD_WHEEL))
    assert is_art_hook(f'uvx --from {_OLD_GIT} art hook user-prompt')
    assert is_art_hook(f'uvx --from {_OLD_GIT} art-mcp')
    for command in ("my-lint", None, 3, "",
                    "uvx --from ruff ruff check",                       # uvx, not ART
                    "uvx --from other-pkg other hook user-prompt",
                    'uvx --from "art-mcp" art-mcp',                      # another project's art-mcp
                    f"art hook user-prompt",                             # no uvx --from
                    f"echo uvx --from {_OLD_GIT}"):
        assert not is_art_hook(command), command


def test_art_hooks_codex_refuses_to_rewrite_a_broken_file(tmp_path, monkeypatch):
    from harness import entry

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "hooks.json").write_text("{not json", encoding="utf-8")
    assert entry.main(["hooks", "codex", "--write"]) == 2
    assert (tmp_path / "hooks.json").read_text(encoding="utf-8") == "{not json"


# ── the Codex plugin carries the hooks (#290) ────────────────────────────────

def test_the_codex_plugin_bundles_the_hooks_pinned_to_this_version():
    """Codex 0.131+ loads a plugin's hooks by default; the manifest names the file Claude Code
    uses, so one file serves both and it pins the wheel of ART_VERSION."""
    from harness import ART_VERSION
    from harness.hooks import SOURCE, WHEEL_URL, hook_config, is_art_hook

    manifest = _json("plugin/.codex-plugin/plugin.json")
    declared = (PLUGIN / manifest["hooks"]).resolve()
    assert declared == (PLUGIN / "hooks" / "hooks.json").resolve() and declared.is_file()
    text = declared.read_text(encoding="utf-8")
    config = json.loads(text)
    assert config == hook_config()
    assert SOURCE.endswith(WHEEL_URL)
    assert f"/v{ART_VERSION}/art_mcp-{ART_VERSION}-py3-none-any.whl" in WHEEL_URL
    assert text.count(WHEEL_URL) == 2
    commands = [h["command"] for groups in config["hooks"].values()
                for g in groups for h in g["hooks"]]
    assert len(commands) == 2 and all(is_art_hook(c) for c in commands)
    # `commandWindows` only swaps the command text on Windows; it cannot pick between
    # PowerShell and cmd.exe, so the one line stays the only line.
    assert "commandWindows" not in text and "command_windows" not in text


def _install_plugin(home, hooks_config=None, enabled=True, plugin="art", market="art"):
    """What `codex plugin add art@art` leaves in a CODEX_HOME: the config entry and the copied
    plugin, hooks file included."""
    from harness.hooks import hook_config

    root = home / "plugins" / "cache" / market / plugin / "0.1.0"
    (root / "hooks").mkdir(parents=True)
    (root / "hooks" / "hooks.json").write_text(
        json.dumps(hook_config() if hooks_config is None else hooks_config), encoding="utf-8")
    with (home / "config.toml").open("a", encoding="utf-8") as f:
        f.write(f'[plugins."{plugin}@{market}"]\nenabled = {"true" if enabled else "false"}\n')


def _art_hook_lines(*configs):
    from harness.hooks import is_art_hook

    return [h["command"] for c in configs for groups in c.get("hooks", {}).values()
            for g in groups for h in g["hooks"] if is_art_hook(h["command"])]


def test_write_with_the_plugin_installed_removes_its_own_hooks_not_the_users(
        tmp_path, monkeypatch, capsys):
    """The duplicate case: `--write` was run before the plugin was installed. Each ART hook must
    fire once, so the plugin's stay and the file's go; everything else in the file is kept."""
    from harness import entry
    from harness.hooks import hook_config

    mine = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "my-lint"}]}],
                      "Stop": [{"hooks": [{"type": "command", "command": "notify"}]}]}}
    path = _codex_config(tmp_path, monkeypatch, mine)
    assert entry.main(["hooks", "codex", "--write"]) == 0          # no plugin yet: file install
    written = json.loads(path.read_text(encoding="utf-8"))
    assert len(_art_hook_lines(written)) == 2

    _install_plugin(tmp_path)
    monkeypatch.setattr("harness.hooks.codex_version", lambda: (0, 149, 0))
    capsys.readouterr()
    assert entry.main(["hooks", "codex", "--write"]) == 0
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after == mine                                            # only the user's remain
    plugin_hooks = json.loads((tmp_path / "plugins/cache/art/art/0.1.0/hooks/hooks.json")
                              .read_text(encoding="utf-8"))
    assert plugin_hooks == hook_config()
    assert len(_art_hook_lines(plugin_hooks, after)) == 2           # one of each, in all
    err = capsys.readouterr().err
    assert "art@art" in err and "2 duplicate" in err

    before = path.read_bytes()
    assert entry.main(["hooks", "codex", "--write"]) == 0          # a second run changes nothing
    assert path.read_bytes() == before


def test_write_with_the_plugin_installed_creates_nothing(tmp_path, monkeypatch, capsys):
    from harness import entry

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr("harness.hooks.codex_version", lambda: None)   # unknown: assume current
    _install_plugin(tmp_path)
    assert entry.main(["hooks", "codex", "--write"]) == 0
    assert not (tmp_path / "hooks.json").exists()
    assert "nothing changed" in capsys.readouterr().err
    # Printing says the same instead of inviting a second install.
    assert entry.main(["hooks", "codex"]) == 0
    assert "already carries" in capsys.readouterr().err


def test_write_still_installs_when_the_plugin_does_not_supply_the_hooks(tmp_path, monkeypatch):
    """The file install stays the path for a Codex without plugin hooks, a disabled plugin, an
    unrelated plugin called `art`, and `--standalone`."""
    from harness import entry
    from harness.hooks import hook_config

    def written(*extra):
        path = tmp_path / "hooks.json"
        path.unlink(missing_ok=True)
        assert entry.main(["hooks", "codex", "--write", *extra]) == 0
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr("harness.hooks.codex_version", lambda: (0, 149, 0))

    _install_plugin(tmp_path, enabled=False)                        # installed but disabled
    assert written() == hook_config()
    (tmp_path / "config.toml").unlink()

    _install_plugin(tmp_path, hooks_config={"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "other-plugin-hook"}]}]}}, market="other")
    assert written() == hook_config()                               # a different plugin's hooks
    (tmp_path / "config.toml").unlink()

    _install_plugin(tmp_path, market="art2")
    assert written("--standalone") == hook_config()                 # asked for explicitly
    assert written() is None                                        # without it, nothing is written

    monkeypatch.setattr("harness.hooks.codex_version", lambda: (0, 125, 0))
    assert written() == hook_config()                               # a Codex too old for them


def test_plugin_detection_ignores_a_missing_or_broken_config(tmp_path):
    from harness.hooks import plugin_with_art_hooks

    assert plugin_with_art_hooks(tmp_path) is None
    (tmp_path / "config.toml").write_text("[plugins\n", encoding="utf-8")
    assert plugin_with_art_hooks(tmp_path) is None
    (tmp_path / "config.toml").write_text('[plugins."art@art"]\nenabled = true\n', encoding="utf-8")
    assert plugin_with_art_hooks(tmp_path) is None                  # enabled, but no cache copy


# ── a pinned, offline-safe source (#256) ─────────────────────────────────────

def _server_source():
    return _json("plugin/.mcp.json")["mcpServers"]["art"]["args"][1]


def test_the_plugin_pins_the_release_wheel_of_this_version():
    """A version bump without a pin bump (or the reverse) fails here, so the plugin never
    runs a wheel that is not the code it was released with."""
    from harness import ART_VERSION
    from harness.hooks import SOURCE, WHEEL_URL

    assert _server_source() == SOURCE
    assert SOURCE == f"art-mcp[ui] @ {WHEEL_URL}"
    assert WHEEL_URL == ("https://github.com/nathansso/agentic_resume_tailoring/releases/"
                         f"download/v{ART_VERSION}/art_mcp-{ART_VERSION}-py3-none-any.whl")
    # The tag the release workflow reacts to is the one in the URL.
    assert f"/v{ART_VERSION}/" in _server_source()
    # Nothing in the plugin still floats on the repository's default branch.
    for path in [PLUGIN / ".mcp.json", PLUGIN / "hooks" / "hooks.json"]:
        assert "git+https" not in path.read_text(encoding="utf-8"), path


def test_a_hook_line_cannot_fail_or_wait_on_the_network():
    from harness.hooks import SOURCE, hook_command

    line = hook_command("user-prompt")
    # Offline: only what the server's start cached. `; exit 0`: uvx failing before
    # `art` runs (no uvx, cold cache) still exits 0.
    assert line.startswith("uvx --offline --from ")
    assert line.endswith("art hook user-prompt; exit 0")
    assert f'"{SOURCE}"' in line
    # Plain words, double quotes and `;`, so sh, Git Bash and PowerShell all run it.
    for token in ("||", "&&", "2>", "$", "'", "`"):
        assert token not in line
    for event in _json("plugin/hooks/hooks.json")["hooks"].values():
        for group in event:
            for hook in group["hooks"]:
                assert "--offline" in hook["command"] and hook["command"].endswith("; exit 0")


def test_art_mcp_subcommand_runs_the_server_with_its_arguments(monkeypatch):
    from harness import entry, mcp_server

    seen = []
    monkeypatch.setattr(mcp_server, "main", lambda argv=None: seen.append(argv))
    assert entry.main(["mcp", "--allow-writes"]) == 0
    assert seen == [["--allow-writes"]]


def test_art_hook_exits_zero_whatever_breaks(monkeypatch, capsys):
    from harness import entry

    def boom(*a, **k):
        raise SystemExit("no store")                 # SystemExit is not an Exception

    monkeypatch.setattr("harness.runtime.bootstrap", boom)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert entry.main(["hook", "user-prompt"]) == 0
    assert capsys.readouterr().out == ""

    monkeypatch.setattr("harness.hooks.main", boom)  # even the hook module failing
    assert entry.main(["hook", "session-start"]) == 0
    assert capsys.readouterr().out == ""


def test_the_release_workflow_builds_the_editor_into_a_tagged_wheel():
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert re.search(r"tags:\s*\[\"v\*\"\]", text) and "workflow_dispatch:" in text
    # The wheel is built after the editor, and checked for it.
    assert text.index("npm --prefix web/frontend ci") < text.index("uv build")
    assert "web/static/index.html" in text
    # Only a tag publishes; a manual run is a dry run.
    release = text[text.index("release:\n    name"):]
    assert "if: startsWith(github.ref, 'refs/tags/v')" in release
    assert "gh release create" in release and "contents: write" in release
    assert "contents: write" not in text[:text.index("release:\n    name")]


def test_every_art_ui_command_in_the_docs_runs_the_pinned_wheel():
    """`/art:open`, the skill and INSTALL tell the user one `art ui` command, and it is the
    release wheel with the editor in it (#255), not a git install, which has none."""
    from harness.hooks import SOURCE

    files = [PLUGIN / "commands" / "open.md", PLUGIN / "skills" / "art-tailor" / "SKILL.md",
             ROOT / "INSTALL.md"]
    for path in files:
        text = path.read_text(encoding="utf-8")
        lines = [ln.strip() for ln in text.splitlines() if " art ui" in ln and "uvx" in ln]
        assert lines, path
        for ln in lines:
            if "dist/" in ln:                       # INSTALL's build-it-yourself line
                continue
            assert ln.startswith(f'uvx --from "{SOURCE}" art ui'), (path.name, ln)
    for path in [*PLUGIN.rglob("*.md"), *PLUGIN.rglob("*.json")]:
        assert "git+https" not in path.read_text(encoding="utf-8"), path
    assert "npm" not in (PLUGIN / "commands" / "open.md").read_text(encoding="utf-8")
