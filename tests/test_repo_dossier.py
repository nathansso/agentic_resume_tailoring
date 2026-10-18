"""The #397 spike's dossier ladder, built offline from a fixture repo (no network, no API).

`eval/repo_dossier/ladder.py` turns a clone into the seven cumulative writer inputs, from the
README alone to the full code. These tests build a two-author git repo in a temp folder and check
that each rung carries what it should and nothing it shouldn't.
"""

import json
import os
import shutil
import subprocess

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
