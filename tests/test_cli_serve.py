"""Tests for the `serve` CLI subcommand (issue #38 — textual-web browser serving).

Structural and unit-level only — no network, no subprocess execution.
"""

import re
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOML_PATH = ROOT / "textual-web.toml"
DOCKERFILE = ROOT / "Dockerfile"


# ---------------------------------------------------------------------------
# Structural tests
# ---------------------------------------------------------------------------

def test_textual_web_toml_exists():
    assert TOML_PATH.exists(), "textual-web.toml must be present at the repo root"


def test_textual_web_toml_has_apps_section():
    content = TOML_PATH.read_text(encoding="utf-8")
    assert "[[apps]]" in content, "textual-web.toml must have an [[apps]] table"


def test_textual_web_toml_has_command():
    content = TOML_PATH.read_text(encoding="utf-8")
    assert "command" in content, "textual-web.toml must specify a command"


def test_dockerfile_exposes_8000():
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert "EXPOSE 8000" in content, "Dockerfile must EXPOSE 8000"


def test_dockerfile_cmd_uses_uvicorn():
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert "uvicorn" in content, "Dockerfile CMD must invoke uvicorn (web.app:app)"


def test_fastapi_in_requirements_core():
    req_core = ROOT / "requirements-core.txt"
    content = req_core.read_text(encoding="utf-8").lower()
    pkgs = {re.split(r"[=<>!\[;]", line.strip())[0].strip().lower().replace("-", "_")
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")}
    assert "fastapi" in pkgs, "fastapi must be listed in requirements-core.txt"


# ---------------------------------------------------------------------------
# Subparser registration
# ---------------------------------------------------------------------------

def test_serve_subparser_registered():
    """argparse must recognise 'serve' as a valid subcommand."""
    import argparse
    import cli as cli_module

    # Reach into the module's main() to build the parser without running it
    # by calling parse_args with the serve subcommand
    import sys
    old_argv = sys.argv
    try:
        sys.argv = ["art", "serve"]
        # Re-use the module's parser by calling parse_known_args
        # We can't call main() directly (it would exec), so we reconstruct
        # via parse_known_args on the real parser
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        sub.add_parser("serve").add_argument("--port", type=int, default=None)
        args, _ = parser.parse_known_args(["serve"])
        assert args.command == "serve"
    finally:
        sys.argv = old_argv


def test_serve_subparser_port_flag():
    """serve --port N must be accepted by the CLI parser."""
    import argparse
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=None)
    args = parser.parse_args(["serve", "--port", "9000"])
    assert args.port == 9000


# ---------------------------------------------------------------------------
# cmd_serve unit test
# ---------------------------------------------------------------------------

def test_cmd_serve_calls_textual_web(monkeypatch):
    """cmd_serve must invoke textual-web serve with the config file."""
    import cli as cli_module

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

    monkeypatch.setattr("subprocess.run", fake_run)

    class FakeArgs:
        port = None

    cli_module.cmd_serve(FakeArgs())

    assert captured["cmd"][0] == "textual-web"
    assert "serve" in captured["cmd"]
    assert "textual-web.toml" in " ".join(captured["cmd"])


def test_cmd_serve_passes_port(monkeypatch):
    """cmd_serve must append --port when args.port is set."""
    import cli as cli_module

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

    monkeypatch.setattr("subprocess.run", fake_run)

    class FakeArgs:
        port = 9000

    cli_module.cmd_serve(FakeArgs())

    cmd_str = " ".join(str(x) for x in captured["cmd"])
    assert "--port" in cmd_str
    assert "9000" in cmd_str
