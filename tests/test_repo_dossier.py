"""The #397 spike's dossier ladder, built offline from a fixture repo (no network, no API).

`eval/repo_dossier/ladder.py` turns a clone into the seven cumulative writer inputs, from the
README alone to the full code. These tests build a two-author git repo in a temp folder and check
that each rung carries what it should and nothing it shouldn't.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from eval.repo_dossier.ladder import RUNGS, Budgets, RepoInput, build_ladder, judge_context
from eval.repo_dossier.symbols import build_symbol_map, tree_sitter_available

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

USER = ("Ada Lovelace", "ada@example.com")
OTHER = ("Charles Babbage", "charles@example.com")

CORE = '''import numpy as np
from pkg.util import normalize


def load_data(path):
    return np.loadtxt(path)


class Model:
    def fit(self, rows):
        return normalize(rows)
'''
UTIL = '''def normalize(rows):
    total = sum(rows)
    return [r / total for r in rows]
'''
APP = '''import React from "react";
import { z } from "zod";

export function renderChart(points: number[]): string {
  return points.join(",");
}

export interface ChartProps { points: number[] }
'''
NOTEBOOK = {
    "cells": [
        {"cell_type": "markdown", "source": ["# Findings\n", "Recall@10 rose to 0.42."]},
        {"cell_type": "code", "source": ["import pandas as pd\n", "print('rows', 1234)"],
         "outputs": [{"output_type": "stream", "text": ["rows 1234\n"]}]},
    ],
    "metadata": {}, "nbformat": 4, "nbformat_minor": 5,
}


def _git(repo, *args, author=None):
    env = dict(os.environ)
    if author:
        env.update(GIT_AUTHOR_NAME=author[0], GIT_AUTHOR_EMAIL=author[1],
                   GIT_COMMITTER_NAME=author[0], GIT_COMMITTER_EMAIL=author[1])
    subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
                   check=True, capture_output=True, env=env)


def _write(repo, path, text):
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def fixture_repo(tmp_path):
    repo = tmp_path / "demo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _write(repo, "README.md", "# Demo\nA demo that forecasts chart points.\n")
    _write(repo, "requirements.txt", "numpy\npandas\n")
    _write(repo, "pkg/util.py", UTIL)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Add the normalize helper", author=OTHER)

    _write(repo, "pkg/core.py", CORE)
    _write(repo, "app.ts", APP)
    _write(repo, "analysis.ipynb", json.dumps(NOTEBOOK))
    _write(repo, "data/points.csv", "x,y\n1,2\n3,4\n")
    _write(repo, "package-lock.json", "{}")
    _write(repo, "node_modules/left-pad/index.js", "module.exports = 1;")
    (repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    _git(repo, "add", "-A", "-f")
    _git(repo, "commit", "-q", "-m", "Build the model and the chart", author=USER)

    _write(repo, "pkg/core.py", CORE + "\n\ndef score(model):\n    return model.fit([1, 2])\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Score the model", author=USER)
    return repo


@pytest.fixture
def ladder(fixture_repo):
    return build_ladder(RepoInput(fixture_repo, "ada/demo", "Ada Lovelace", [USER[1]], meta=None))


def test_every_rung_is_built(ladder):
    assert list(ladder.rungs) == RUNGS


def test_rung_1_is_the_readme_alone(ladder):
    r1 = ladder.rungs["1"]
    assert "forecasts chart points" in r1
    assert "## Metadata" not in r1 and "## File tree" not in r1


def test_rung_2_adds_metadata_manifests_and_imports(ladder):
    r2 = ladder.rungs["2"]
    assert "## Metadata" in r2
    assert "### requirements.txt" in r2 and "pandas" in r2
    imports = r2.split("Detected imports (most used first): ")[1].splitlines()[0]
    assert {"numpy", "react", "zod", "pandas"} <= set(imports.split(", "))
    assert "## File tree" not in r2


def test_rung_3_adds_a_filtered_tree(ladder):
    tree = ladder.sections["tree"]
    assert "pkg/core.py" in tree and "data/points.csv" in tree
    assert "node_modules" not in tree and "package-lock.json" not in tree
    assert "## Authorship" not in ladder.rungs["3"]


def test_rung_4_adds_the_users_authorship_only(ladder):
    auth = ladder.sections["authorship"]
    assert "User commits: 2 of 3" in auth
    assert "pkg/core.py" in auth and "Score the model" in auth
    assert "Add the normalize helper" not in auth   # another author's commit
    assert "pkg/util.py" not in auth                # another author's file


def test_rung_5_adds_a_symbol_map_and_5r_its_regex_twin(ladder):
    smap, rmap = ladder.sections["symbols"], ladder.sections["symbols_regex"]
    for m in (smap, rmap):
        assert "def load_data(path):" in m and "export function renderChart" in m
    assert ("tree-sitter" in smap) == tree_sitter_available()
    assert "regex" in rmap


def test_rung_6_adds_the_users_most_changed_source(ladder):
    samples = ladder.sections["samples"]
    assert "### pkg/core.py" in samples and "def score(model)" in samples
    assert "### pkg/util.py" not in samples


def test_rung_7_carries_the_full_code(ladder):
    full = ladder.rungs["7"]
    assert "def normalize(rows)" in full                 # the other author's code too
    assert "Recall@10 rose to 0.42" in full and "rows 1234" in full   # notebook text and output
    assert "(data file, ~3 lines" in full                # data shown by shape
    assert "left-pad" not in full and "PNG" not in full
    assert "## Symbol map" not in full and "## Sampled source files" not in full
    assert ladder.stats["full_code"]["truncated"] is False


def test_rungs_are_cumulative(ladder):
    r = ladder.rungs
    for lower, upper in [("1", "2"), ("2", "3"), ("3", "4"), ("4", "5"), ("5", "6"), ("4", "5r"), ("4", "7")]:
        assert r[upper].startswith(r[lower]), (lower, upper)
        assert len(r[upper]) > len(r[lower])


def test_the_full_code_cap_is_recorded(fixture_repo):
    small = build_ladder(RepoInput(fixture_repo, "ada/demo", "Ada", [USER[1]]), Budgets(full_code_chars=300))
    assert small.stats["full_code"]["truncated"] is True
    assert "capped at 300 chars" in small.rungs["7"]


def test_the_judge_sees_every_authors_history(fixture_repo):
    text, stats = judge_context(RepoInput(fixture_repo, "ada/demo", "Ada", [USER[1]]))
    assert "Charles Babbage" in text and "Add the normalize helper" in text
    assert "Ada Lovelace" in text and not stats["truncated"]


def test_the_ladder_is_the_same_under_any_hash_seed(fixture_repo):
    """Set iteration order must not leak into a dossier: imports and the symbol map ranking are
    built from sorted collections, so cached writer calls stay valid across runs."""
    root = Path(__file__).resolve().parent.parent
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(root)!r})\n"
        "from eval.repo_dossier.ladder import RepoInput, build_ladder\n"
        f"lad = build_ladder(RepoInput({str(fixture_repo)!r}, 'ada/demo', 'Ada', [{USER[1]!r}]))\n"
        "sys.stdout.write(json.dumps(lad.rungs))\n")
    outs = []
    for seed in ("1", "2", "3"):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONDOTENV_DISABLED="1", PYTHON_DOTENV_DISABLED="1")
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=root)
        assert done.returncode == 0, done.stderr
        outs.append(done.stdout)
    assert outs[0] == outs[1] == outs[2]


def test_the_symbol_map_is_seeded_by_the_users_files():
    """What the user's files call ranks first: seeding the other file flips the order."""
    sources = {
        "a.py": "def alpha_helper():\n    pass\n",
        "b.py": "def beta_helper():\n    pass\n",
        "uses_a.py": "alpha_helper()\n",
        "uses_b.py": "beta_helper()\n",
    }
    for seed, first in (("uses_a.py", "a.py:"), ("uses_b.py", "b.py:")):
        for backend in ("regex", "auto"):
            smap = build_symbol_map(sources, {seed: 10.0}, token_budget=500, backend=backend)
            assert smap.text.startswith(first), (backend, smap.text)
    tiny = build_symbol_map(sources, None, token_budget=5, backend="regex")
    assert tiny.rendered < 2   # the budget binds


# ── the #397 follow-up: variants at matched budgets, more backends, attribution ──

from eval.repo_dossier import attribution as attr  # noqa: E402
from eval.repo_dossier import run as runner  # noqa: E402
from eval.repo_dossier.symbols import build_symbol_map_v2, ctags_binary, spans_for  # noqa: E402
from eval.repo_dossier.variants import VARIANTS, build_variant, fit, make_context  # noqa: E402

PRS = [{"number": 7, "title": "feat: score the model", "merged_at": "2026-01-02T00:00:00Z",
        "additions": 3, "deletions": 0, "merge_commit_sha": "abcdef1234567",
        "body": "Adds <!-- template --> a score() helper.", "files": ["pkg/core.py"]}]


@pytest.fixture
def ctx(fixture_repo):
    return make_context(RepoInput(fixture_repo, "ada/demo", "Ada Lovelace", [USER[1]]), PRS)


def test_every_variant_builds_within_its_budget_and_names_the_commit(ctx, fixture_repo):
    head = subprocess.run(["git", "-C", str(fixture_repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    for vid, v in VARIANTS.items():
        text, stats = build_variant(ctx, vid)
        assert text.startswith(f"# Repository dossier: ada/demo at commit {head[:12]}"), vid
        assert stats["est_tokens"] <= v.budget, vid
        assert "## README" in text, vid
        assert ("## Symbol map" in text) == (v.sym_share != 0.0), vid


def test_fit_keeps_short_sections_and_cuts_long_ones_at_a_line():
    long = "## README\n" + "\n".join(f"line {i} " + "x" * 50 for i in range(2000))
    out, alloc = fit([("readme", long), ("metadata", "## Metadata\nshort"), ("tree", "## File tree\na.py")],
                     budget_tokens=1000)
    assert out["metadata"] == "## Metadata\nshort" and out["tree"] == "## File tree\na.py"
    assert out["readme"].endswith("…[section cut to fit the dossier's token budget]")
    assert len(out["readme"]) <= alloc["readme"]
    # what the short sections left is handed to the long one
    assert alloc["readme"] > 1000 * 2.3 * 0.30
    assert sum(len(t) for t in out.values()) <= 1000 * 2.3


def test_symbol_lines_cite_their_span(ctx):
    text, _ = build_variant(ctx, "5r-10k")
    assert "pkg/core.py:" in text
    assert "L5-6: def load_data(path):" in text


def test_the_ast_backend_has_exact_spans_and_full_signatures():
    code = ("@app.get('/x')\ndef handler(\n    a: int,\n    b=2,\n) -> str:\n    return 'x'\n\n\n"
            "class K:\n    def m(self):\n        pass\n")
    spans, used = spans_for("ast", {"m.py": code})
    defs = {n: (s, e) for n, s, e in spans["m.py"][0]}
    assert defs["handler"] == (0, 5)       # from the decorator to the last body line
    assert defs["K"] == (8, 10) and used == {"ast"}
    smap = build_symbol_map_v2({"m.py": code}, None, 500, "ast")
    assert "@app.get('/x') def handler( a: int, b=2, ) -> str:" in smap.text


def test_regex_spans_close_python_definitions_by_indent():
    code = ("class A:\n    def one(self):\n        return 1\n\n    def two(self):\n        return 2\n\n\n"
            "def top():\n    pass\n")
    smap = build_symbol_map_v2({"a.py": code}, None, 500, "regex")
    spans = {name: (s, e) for _, name, s, e in smap.ranked}
    assert spans["A"] == (0, 5) and spans["one"] == (1, 2) and spans["top"] == (8, 9)


@pytest.mark.skipif(not tree_sitter_available(), reason="needs tree-sitter")
def test_the_tree_sitter_tags_backend_reads_each_grammars_tags_query():
    spans, used = spans_for("ts-tags", {"pkg/core.py": CORE, "app.ts": APP})
    names = {n for n, _, _ in spans["pkg/core.py"][0]}
    assert {"load_data", "Model", "fit"} <= names and "ts-tags" in used
    assert "normalize" in {n for n, _ in spans["pkg/core.py"][1]}   # a call is a reference
    assert "renderChart" in {n for n, _, _ in spans["app.ts"][0]}


def test_the_pygments_backend_finds_definitions():
    pytest.importorskip("pygments")
    spans, used = spans_for("pygments", {"pkg/core.py": CORE})
    assert {"load_data", "Model"} <= {n for n, _, _ in spans["pkg/core.py"][0]} and used == {"pygments"}


@pytest.mark.skipif(ctags_binary() is None, reason="needs Universal Ctags (ART_CTAGS or PATH)")
def test_the_ctags_backend_reads_files_on_disk(fixture_repo):
    spans, used = spans_for("ctags", {"pkg/core.py": CORE}, fixture_repo)
    assert {"load_data", "Model"} <= {n for n, _, _ in spans["pkg/core.py"][0]} and used == {"ctags"}


def test_a_missing_ctags_falls_back_to_regex(monkeypatch, fixture_repo):
    monkeypatch.setenv("ART_CTAGS", "")
    monkeypatch.setattr("shutil.which", lambda name: None)
    spans, used = spans_for("ctags", {"pkg/core.py": CORE}, fixture_repo)
    assert used == {"regex"} and spans["pkg/core.py"][0]


def test_blame_tags_each_definition_with_who_wrote_it(fixture_repo):
    index = attr.BlameIndex(fixture_repo, {USER[1]})
    mine = index.ownership("pkg/core.py", 4, 5)          # load_data, the user's
    theirs = index.ownership("pkg/util.py", 0, 2)        # normalize, the other author's
    assert mine.user_share == 1.0 and theirs.user_share == 0.0
    assert theirs.top_author == "Charles Babbage" and len(theirs.top_sha) == 40
    assert index.annotate("pkg/util.py", "normalize", 0, 2).startswith("[mostly Charles Babbage 100%")
    assert "introduced by Charles Babbage" in index.annotate("pkg/util.py", "normalize", 0, 2)
    assert "introduced by you" in index.annotate("pkg/core.py", "load_data", 4, 5)


def test_attribution_variants_name_other_authors_work(ctx):
    text, stats = build_variant(ctx, "own-10k")
    assert "[mostly Charles Babbage" in text
    others = text.split("## Built by other authors")[1]
    assert "pkg/util.py:L1-3 normalize: Charles Babbage 100%" in others
    assert "load_data" not in others
    assert stats["blamed_files"] >= 2
    plain, _ = build_variant(ctx, "5r-10k")
    assert "Charles Babbage" not in plain and "## Built by other authors" not in plain


def test_the_attrib_map_keeps_only_the_users_own_and_contested_definitions(ctx):
    text, stats = build_variant(ctx, "blame-20k-attrib")
    smap = text.split("## Symbol map")[1].split("\n## ")[0]
    assert "load_data" in smap and "score" in smap          # the user's own
    assert "normalize" not in smap                           # another author's, in a file the user never touched
    assert "normalize: Charles Babbage" in text.split("## Built by other authors")[1]
    assert stats["symbol_rendered"] <= 40


def test_the_symbol_share_sweep_resizes_or_drops_the_map(ctx):
    none, _ = build_variant(ctx, "blame-20k-s0")
    assert "## Symbol map" not in none and "## Built by other authors" in none
    from eval.repo_dossier.variants import SHARES
    long = [("readme", "## README\n" + "x\n" * 30_000), ("symbols", "## Symbol map\n" + "y\n" * 30_000)]
    _, small = fit(long, 1000, shares=dict(SHARES, symbols=0.10))
    _, large = fit(long, 1000, shares=dict(SHARES, symbols=0.40))
    assert small["symbols"] < large["symbols"] and small["readme"] > large["readme"]


def test_blame_is_skipped_for_a_single_author_repo(tmp_path):
    repo = tmp_path / "solo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _write(repo, "a.py", "def solo_fn():\n    return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "solo", author=USER)
    c = make_context(RepoInput(repo, "ada/solo", "Ada", [USER[1]]), None)
    text, stats = build_variant(c, "own-10k")
    assert stats["blamed_files"] == 0 and "[you" not in text
    assert "(single-author repository)" in text and "(not fetched)" in text


def test_commit_subjects_sit_under_each_file(ctx):
    text, _ = build_variant(ctx, "commits-10k")
    section = text.split("## Authorship by file")[1].split("\n## ")[0]
    core = section.split("pkg/core.py")[1]
    assert "Score the model" in core and "Build the model and the chart" in core
    assert "Add the normalize helper" not in section


def test_conventional_commit_types_are_parsed():
    assert attr.conventional_type("feat(api): add x") == "feat"
    assert attr.conventional_type("fix!: break y") == "fix"
    assert attr.conventional_type("Merge branch 'main'") is None


def test_the_prs_section_cites_number_merge_commit_and_files():
    text = attr.prs_section(PRS, "Ada")
    assert "#7 (2026-01-02, +3 -0, abcdef1) feat: score the model" in text
    assert "template" not in text and "files: pkg/core.py" in text
    assert "(none found)" in attr.prs_section([], "Ada")


def test_the_variants_are_the_same_under_any_hash_seed(fixture_repo):
    root = Path(__file__).resolve().parent.parent
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(root)!r})\n"
        "from eval.repo_dossier.ladder import RepoInput\n"
        "from eval.repo_dossier.variants import VARIANTS, build_variant, make_context\n"
        f"ctx = make_context(RepoInput({str(fixture_repo)!r}, 'ada/demo', 'Ada', [{USER[1]!r}]), [])\n"
        "sys.stdout.write(json.dumps({v: build_variant(ctx, v)[0] for v in VARIANTS}))\n")
    outs = []
    for seed in ("1", "2", "3"):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONDOTENV_DISABLED="1", PYTHON_DOTENV_DISABLED="1")
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=root)
        assert done.returncode == 0, done.stderr
        outs.append(done.stdout)
    assert outs[0] == outs[1] == outs[2]


def test_the_tree_sitter_map_takes_the_earliest_of_a_repeated_name():
    code = "class A:\n    def __init__(self, a):\n        pass\n\n\nclass B:\n    def __init__(self, b):\n        pass\n"
    smap = build_symbol_map({"m.py": code}, None, token_budget=500, backend="auto")
    assert "def __init__(self, a):" in smap.text and "self, b" not in smap.text


def test_the_cli_backend_runs_claude_on_the_subscription_and_caches(tmp_path, monkeypatch):
    from eval.repo_dossier import llm
    seen = []

    def fake_run(cmd, input, env, cwd, **kw):
        seen.append({"cmd": cmd, "input": input, "env": env, "cwd": cwd,
                     "system": Path(cmd[cmd.index("--system-prompt-file") + 1]).read_text(encoding="utf-8")})
        out = {"type": "result", "subtype": "success", "is_error": False, "stop_reason": "end_turn",
               "structured_output": {"description": "d", "bullets": ["b"]}, "total_cost_usd": 0.01,
               "usage": {"input_tokens": 5, "output_tokens": 7, "cache_creation_input_tokens": 0,
                         "cache_read_input_tokens": 0}}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(subprocess, "run", fake_run)
    client = llm.CliClient(tmp_path / "cache", llm.Ledger(float("inf")), tmp_path / "wd", exe="claude")
    parsed, usage, cached = client.write("ada/demo", "Ada", "## README\nx")
    assert parsed == {"description": "d", "bullets": ["b"]} and not cached and usage["list_usd"] == 0.01
    call = seen[0]
    assert "ANTHROPIC_API_KEY" not in call["env"]           # never falls back to API billing
    cmd = call["cmd"]
    assert cmd[cmd.index("--tools") + 1] == "" and "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--model") + 1] == llm.WRITER_MODEL and cmd[cmd.index("--effort") + 1] == "medium"
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == llm.WRITER_SCHEMA
    assert call["system"] == llm.WRITER_SYSTEM and "## README" in call["input"]
    assert call["cwd"] == tmp_path / "wd"
    # the same request is answered from the cache, without a second process
    assert client.write("ada/demo", "Ada", "## README\nx")[2] is True and len(seen) == 1
    rec = json.loads(next((tmp_path / "cache").glob("write-*.json")).read_text(encoding="utf-8"))
    assert rec["backend"] == "claude-cli"


def test_the_plain_pagerank_matches_networkx():
    """art-mcp ships networkx without SciPy, and networkx's pagerank needs SciPy; the variants'
    own power iteration must give networkx's answer."""
    pytest.importorskip("scipy")
    import networkx as nx

    from eval.repo_dossier.symbols import pagerank
    g = nx.MultiDiGraph()
    g.add_nodes_from(["a", "b", "c", "d", "lonely"])
    for u, v, w in [("a", "b", 2.0), ("a", "b", 1.0), ("b", "c", 1.0), ("c", "a", 0.5), ("d", "c", 3.0)]:
        g.add_edge(u, v, weight=w)
    pers = {"a": 0.7, "b": 0.0, "c": 0.0, "d": 0.3, "lonely": 0.0}
    mine = pagerank(g, pers)
    ref = nx.pagerank(g, weight="weight", personalization=pers, dangling=pers)
    assert mine.keys() == ref.keys()
    assert all(abs(mine[k] - ref[k]) < 1e-6 for k in ref)
    assert pagerank(g, None) == pagerank(g, None)


def test_a_crlf_checkout_gives_the_same_dossier(ctx, fixture_repo):
    """A Windows clone (core.autocrlf) has CRLF where a Linux clone has LF: same dossier."""
    from eval.repo_dossier import ladder as lad
    before = {v: build_variant(ctx, v)[0] for v in ("5r-10k", "own-10k")}
    for p in ("README.md", "pkg/core.py", "pkg/util.py", "app.ts", "requirements.txt"):
        f = fixture_repo / p
        f.write_bytes(f.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    lad._SIZES.clear()
    again = make_context(RepoInput(fixture_repo, "ada/demo", "Ada Lovelace", [USER[1]]), PRS)
    assert {v: build_variant(again, v)[0] for v in before} == before


def test_a_symlinked_readme_is_read_through_git(tmp_path):
    """A Windows checkout without core.symlinks writes a symlink as a file holding its target;
    the reader follows the link through git, as a Linux checkout would."""
    from eval.repo_dossier import ladder as lad
    repo = tmp_path / "linked"
    repo.mkdir()
    _git(repo, "init", "-q")
    _write(repo, "docs/index.rst", "Linked\n======\nThe real readme text.\n")
    _git(repo, "add", "-A")
    blob = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input="docs/index.rst",
                          capture_output=True, text=True).stdout.strip()
    _git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},README.rst")
    _git(repo, "commit", "-q", "-m", "link the readme", author=USER)
    _write(repo, "README.rst", "docs/index.rst")   # what a Windows checkout leaves on disk
    files = lad.list_files(repo)
    assert "README.rst" in files
    section = lad.readme_section(repo, files, Budgets())
    assert "The real readme text." in section and "## README (README.rst)" in section


def test_the_cli_backend_refuses_a_folder_under_a_claude_md(tmp_path):
    from eval.repo_dossier import llm
    (tmp_path / "CLAUDE.md").write_text("private guidance", encoding="utf-8")
    with pytest.raises(RuntimeError, match="CLAUDE.md"):
        llm.CliClient(tmp_path / "cache", llm.Ledger(1.0), tmp_path / "wd", exe="claude")


def _claims(n_ok, n_role_bad):
    return ([{"label": "supported", "about_user_role": True}] * n_ok
            + [{"label": "contradicted", "about_user_role": True}] * n_role_bad)


def test_aggregate_reports_misattribution_against_the_baseline():
    results = {}
    for i, (base_bad, own_bad) in enumerate([(3, 0), (2, 1), (4, 0)]):
        rows = [{"rung": "5r-10k", "sample": 0, "claims": _claims(7, base_bad),
                 "writer_usage": {"input_tokens": 9000}},
                {"rung": "own-10k", "sample": 0, "claims": _claims(10 - own_bad, own_bad),
                 "writer_usage": {"input_tokens": 9500}},
                {"rung": "7", "sample": 0, "claims": _claims(10, 0), "writer_usage": {"input_tokens": 90000}}]
        results[f"r/{i}"] = {"group": "multi", "rows": rows, "n_facts": 0}
    agg = runner.aggregate(runner.per_repo_metrics(results))
    assert agg["5r-10k"]["misattribution"] == pytest.approx((0.3 + 2 / 9 + 4 / 11) / 3)
    vs = agg["own-10k"]["vs_baseline"]["contradicted_role"]
    assert vs["mean"] < 0 and vs["ci"][1] < 0 and vs["repos"] == 3
    assert "vs_baseline" not in agg["5r-10k"]


def test_the_spotcheck_summary_gives_wilson_intervals():
    checked = ([{"rung": "own-10k", "label": "supported", "my_label": "supported", "about_user_role": True}] * 9
               + [{"rung": "1", "label": "contradicted", "my_label": "unsupported", "about_user_role": True}])
    results = {"r/x": {"rows": [
        {"rung": "own-10k", "claims": [{"label": "supported", "about_user_role": True}] * 30},
        {"rung": "1", "claims": [{"label": "contradicted", "about_user_role": True}] * 10}]}}
    s = runner.spotcheck_summary(checked, results)
    assert s["agree"] == 9 and s["rate"] == 0.9
    lo, hi = s["rate_ci"]
    assert 0.55 < lo < 0.9 < hi <= 1.0
    assert s["agree_supported_vs_not"] == 10
    assert s["reweighted_to_population"]["exact"] == pytest.approx(0.75)
    assert s["confusion"] == {"contradicted->unsupported": 1}
