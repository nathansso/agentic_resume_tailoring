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

    entry.main(["hooks", "codex", "--write", "--from", "art-mcp"])    # idempotent
    again = json.loads((tmp_path / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert again == got

    assert entry.main(["hooks", "codex"]) == 0                       # print only
    assert json.loads(capsys.readouterr().out) == hook_config()


def test_art_hooks_codex_refuses_to_rewrite_a_broken_file(tmp_path, monkeypatch):
    from harness import entry

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "hooks.json").write_text("{not json", encoding="utf-8")
    assert entry.main(["hooks", "codex", "--write"]) == 2
    assert (tmp_path / "hooks.json").read_text(encoding="utf-8") == "{not json"


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
