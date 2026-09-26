"""The installable package (issue #194): `uvx art-mcp` without torch.

`pyproject.toml` declares a light default install (what the harness imports)
with heavy pieces behind extras. These tests hold that line from the source
tree: the declared dependencies, the entry points, that the harness's whole
import graph stays inside the wheel and off the heavy packages, and that a
first run on a clean data directory creates the store and a profile. The CI
`package` job then builds the wheel and runs `scripts/smoke_art_mcp.py` in a
fresh venv holding nothing else.
"""

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
PROJECT = PYPROJECT["project"]
HEAVY = {"torch", "sentence-transformers", "transformers", "docling", "langchain",
         "langchain-core", "langchain-openai", "langchain-anthropic", "langgraph", "openai",
         "anthropic", "scikit-learn", "nltk", "supabase", "fastapi", "uvicorn"}
HEAVY_MODULES = ("torch", "sentence_transformers", "transformers", "docling", "langchain",
                 "langchain_core", "langgraph", "openai", "anthropic", "sklearn", "nltk",
                 "fastapi", "uvicorn", "supabase")


def _name(req: str) -> str:
    return re.split(r"[\s\[<>=!~;]", req, maxsplit=1)[0].lower()


def _pin(path: Path, package: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if _name(line) == package and "==" in line:
            return line.split("==", 1)[1].strip()
    raise AssertionError(f"{package} is not pinned in {path.name}")


def test_the_default_install_carries_nothing_heavy():
    base = {_name(r) for r in PROJECT["dependencies"]}
    assert not base & HEAVY, base & HEAVY
    assert {"mcp", "sqlmodel", "pydantic", "numpy", "networkx", "requests",
            "python-dotenv"} <= base


def test_heavy_pieces_live_in_the_extras():
    extras = {k: {_name(r) for r in v} for k, v in PROJECT["optional-dependencies"].items()}
    assert "sentence-transformers" in extras["embed"]
    assert "docling" in extras["pdf"]
    assert {"fastapi", "uvicorn"} <= extras["ui"]
    assert "psycopg2-binary" in extras["postgres"]


def test_pins_match_the_requirements_files():
    deps = {_name(r): r for r in PROJECT["dependencies"]}
    assert deps["sqlmodel"] == f"sqlmodel=={_pin(ROOT / 'requirements-lock.txt', 'sqlmodel')}"
    assert deps["mcp"] == f"mcp=={_pin(ROOT / 'requirements-core.txt', 'mcp')}"


def test_the_scripts_resolve_to_callables():
    import importlib

    scripts = PROJECT["scripts"]
    assert set(scripts) == {"art", "art-mcp"}
    for target in scripts.values():
        module, func = target.split(":")
        assert callable(getattr(importlib.import_module(module), func))


_GRAPH_PROBE = r"""
import json, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
for m in ("harness.mcp_server", "harness.cli", "harness.entry", "harness.tools",
          "harness.tree", "harness.ingest", "harness.executor", "harness.render_cache",
          "harness.render", "harness.hooks"):
    __import__(m)
tops = set()
for mod in list(sys.modules.values()):
    f = getattr(mod, "__file__", None)
    if f and Path(f).resolve().is_relative_to(root):
        top = Path(f).resolve().relative_to(root).parts[0]
        if not top.startswith("."):          # the checkout's own .venv
            tops.add(top.removesuffix(".py"))
print(json.dumps({"tops": sorted(tops), "modules": sorted(sys.modules)}))
"""


def test_the_harness_import_graph_stays_in_the_wheel_and_off_heavy_packages():
    env = {**os.environ, "DATABASE_URL": "sqlite://"}
    out = subprocess.run([sys.executable, "-c", _GRAPH_PROBE, str(ROOT)], cwd=ROOT, env=env,
                         capture_output=True, text=True, check=True)
    probe = json.loads(out.stdout.strip().splitlines()[-1])
    shipped = {p.removesuffix(".py")
               for p in PYPROJECT["tool"]["hatch"]["build"]["targets"]["wheel"]["only-include"]}
    assert set(probe["tops"]) <= shipped, set(probe["tops"]) - shipped
    loaded = {m.split(".")[0] for m in probe["modules"]}
    assert not loaded & set(HEAVY_MODULES), loaded & set(HEAVY_MODULES)


def _cli(args, data_dir):
    env = {k: v for k, v in os.environ.items()
           if k not in ("DATABASE_URL", "ART_MCP_DATABASE_URL", "ART_MCP_USER_ID")}
    env["ART_DATA_DIR"] = str(data_dir)
    return subprocess.run([sys.executable, "-m", "harness.entry", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)


def test_a_first_run_on_a_clean_data_dir_creates_the_store_and_a_profile(tmp_path):
    """Starts empty: a named data dir never inherits a checkout's art.db."""
    data = tmp_path / "fresh"
    out = _cli(["list_items"], data)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {"error": None, "items": []}
    assert (data / "art.db").is_file()
    uid = (data / "active_profile_id").read_text().strip()
    # The next call binds the same profile rather than creating another.
    again = _cli(["get_profile"], data)
    assert json.loads(again.stdout)["name"] == "Default User"
    assert (data / "active_profile_id").read_text().strip() == uid


def test_art_dispatches_tools_and_ui(monkeypatch, capsys):
    from harness import entry

    assert entry.main(["--list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    from harness.contract import TOOLS
    assert [t["name"] for t in listed["tools"]] == [t.name for t in TOOLS]

    seen = {}
    import web.local_ui
    monkeypatch.setattr(web.local_ui, "main", lambda argv: seen.setdefault("argv", argv) and 0)
    entry.main(["ui", "--job", "abc", "--no-open"])
    assert seen["argv"] == ["--job", "abc", "--no-open"]


def test_art_ui_without_the_ui_extra_says_how_to_install_it(monkeypatch, capsys):
    import builtins

    from harness import entry

    real = builtins.__import__

    def no_fastapi(name, *a, **k):
        if name == "fastapi":
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_fastapi)
    assert entry.main(["ui"]) == 2
    assert "[ui]" in capsys.readouterr().err


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib")
def test_the_version_is_the_harness_version():
    from harness import ART_VERSION

    pattern = PYPROJECT["tool"]["hatch"]["version"]["pattern"]
    text = (ROOT / PYPROJECT["tool"]["hatch"]["version"]["path"]).read_text(encoding="utf-8")
    assert re.search(pattern, text).group("version") == ART_VERSION
