"""The repo dossier builder (#398): #397's `blame-20k`, built offline from fixture repos.

Every repo here is made in a temp folder with `git`; nothing reaches the network. The builder
reads git objects at a commit, so these cover what #397 measured going wrong: CRLF checkouts,
symlinked READMEs, hash-seed order, unbounded maps and blame, and narrow identity resolution.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from harness.dossier import Seeds, build, fit
from harness.dossier import blame as blame_mod
from harness.dossier import symbols as sym
from harness.dossier.build import BUDGET_TOKENS, CHARS_PER_TOKEN, CUT_MARKER
from harness.dossier.gitread import snapshot
from harness.dossier.identity import resolve

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

ROOT = Path(__file__).resolve().parent.parent
USER = ("Ada Lovelace", "ada@example.com")
USER_ALT = ("ada", "ada.lovelace@work.example")       # same person, another machine
OTHER = ("Charles Babbage", "charles@example.com")

UTIL = '''def normalize(rows):
    total = sum(rows)
    return [r / total for r in rows]


class Scaler:
    def scale(self, x):
        return normalize(x)
'''
UTIL_FIXED = UTIL.replace("total = sum(rows)", "total = sum(rows) or 1")
CORE = '''import numpy as np
from pkg.util import normalize, Scaler


def load_data(path):
    return np.loadtxt(path)


class Model:
    def fit(self, rows):
        return normalize(rows)
'''
APP = '''import React from "react";

export function renderChart(points: number[]): string {
  return points.join(",");
}
'''
TEST = '''from pkg.util import normalize


def test_normalize():
    assert normalize([1, 1]) == [0.5, 0.5]
'''


def git(repo, *args, author=None, inp=None):
    env = dict(os.environ)
    if author:
        env.update(GIT_AUTHOR_NAME=author[0], GIT_AUTHOR_EMAIL=author[1],
                   GIT_COMMITTER_NAME=author[0], GIT_COMMITTER_EMAIL=author[1])
    env.update(GIT_AUTHOR_DATE="2026-01-02T03:04:05+00:00",
               GIT_COMMITTER_DATE="2026-01-02T03:04:05+00:00")
    done = subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false",
                           "-c", "core.autocrlf=false", *args],
                          check=True, capture_output=True, env=env, input=inp)
    return done.stdout.decode().strip()


def write(repo, path, text, newline="\n"):
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.replace("\n", newline).encode())


def make_repo(tmp_path, name="demo"):
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    return repo


@pytest.fixture
def two_authors(tmp_path):
    """Charles writes the util module; Ada writes the core and the app, then fixes util."""
    repo = make_repo(tmp_path)
    write(repo, "README.md", "# Demo\nA demo that forecasts chart points.\n")
    write(repo, "requirements.txt", "numpy\n")
    write(repo, "pkg/util.py", UTIL)
    write(repo, "tests/test_util.py", TEST)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Add the normalize helper", author=OTHER)
    write(repo, "pkg/core.py", CORE)
    write(repo, "app.ts", APP)
    write(repo, "package-lock.json", "{}")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "feat: core model and chart", author=USER)
    write(repo, "pkg/util.py", UTIL_FIXED)
    git(repo, "commit", "-qam", "fix: zero total", author=USER)
    return repo


def ada(**kw):
    return Seeds.make([USER[1]], **kw)


# ── the sections, and their citations ────────────────────────────────────────

def test_the_dossier_has_every_section_in_order_and_names_its_commit(two_authors):
    d = build(two_authors, "ada/demo", ada())
    head = git(two_authors, "rev-parse", "HEAD")
    assert d.commit == head
    assert d.text.startswith(f"# Repository dossier: ada/demo at commit {head[:12]}\n")
    order = ["## README (README.md)", "## Metadata", "## File tree", "## Authorship",
             "## Symbol map", "## Built by other authors"]
    at = [d.text.index(h) for h in order]
    assert at == sorted(at)
    assert "package-lock.json" not in d.text                         # lock files filtered
    assert "requirements.txt  (6B)" in d.text                        # committed size
    assert "Detected imports (most used first): " in d.text and "numpy" in d.text
    assert "User commits: 2 of 3 non-merge commits (67%), 2 author(s)" in d.text


def test_every_mapped_definition_cites_a_line_span_and_its_ownership(two_authors):
    d = build(two_authors, "ada/demo", ada())
    smap = d.text[d.text.index("## Symbol map"):d.text.index("## Built by other authors")]
    defs = [ln for ln in smap.splitlines() if ln.startswith("  ")]
    assert defs and all(re.match(r"^  L\d+(-\d+)?: ", ln) for ln in defs)
    assert all(re.search(r"\[(you \d+%|mostly [^\]]+ \d+%), [0-9a-f]{7}", ln) for ln in defs)
    core = git(two_authors, "log", "--format=%h", "-1", "--", "pkg/core.py")[:7]
    first = git(two_authors, "rev-list", "--max-parents=0", "HEAD")[:7]
    assert f"L9-11: class Model:  [you 100%, {core}; introduced by you {core}]" in smap
    assert (f"L6-8: class Scaler:  [mostly Charles Babbage 100%, {first}; introduced by "
            f"Charles Babbage {first}]") in smap
    assert "mostly Charles Babbage 67%" in smap and "you 33%" in smap   # normalize: reworked


def test_built_by_others_names_the_other_authors_work_and_leaves_out_tests(two_authors):
    d = build(two_authors, "ada/demo", ada())
    others = d.text[d.text.index("## Built by other authors"):]
    first = git(two_authors, "rev-list", "--max-parents=0", "HEAD")[:7]
    assert f"pkg/util.py:L6-8 Scaler: Charles Babbage 100% ({first})" in others
    assert "normalize" not in others          # Ada wrote a third of it: hers to call reworked
    assert "tests/" not in others


def test_authorship_cites_short_shas_and_no_email_reaches_the_dossier(two_authors):
    d = build(two_authors, "ada/demo", ada())
    for sha in git(two_authors, "log", "--format=%h", "--author", USER[1]).split():
        assert f" {sha[:7]}  " in d.text
    assert "@example.com" not in d.text


def test_a_single_author_repo_skips_blame(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    write(repo, "pkg/core.py", CORE)
    write(repo, "pkg/util.py", UTIL)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init", author=USER)
    git(repo, "commit", "-q", "--allow-empty", "-m", "again", author=USER_ALT)

    def no_blame(*a, **k):
        raise AssertionError("blame ran on a single-author repo")
    monkeypatch.setattr(blame_mod, "blame", no_blame)
    d = build(repo, "ada/solo", Seeds.make([USER[1]], names=["ada"]))
    assert "(single-author repository: blame skipped)" in d.text
    assert "[you" not in d.text and d.stats["blamed_files"] == 0
    assert d.stats["authors"] == 1                  # two emails, one person


# ── identity: wider than the login-linked emails ─────────────────────────────

def test_identity_matches_every_email_mailmap_and_display_name(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "a.py", "def a():\n    return 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "a", author=USER)                         # account email
    write(repo, "b.py", "def b():\n    return 2\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "b", author=("Lovelace, A.", "ada@laptop.local"))  # mailmap
    write(repo, "c.py", "def c():\n    return 3\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "c", author=("ADA LOVELACE", "random@host"))  # display name
    write(repo, "d.py", "def d():\n    return 4\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "d", author=("ada", "7+ada@users.noreply.github.com"))
    write(repo, "e.py", "def e():\n    return 5\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "e", author=OTHER)
    write(repo, ".mailmap", "Ada Lovelace <ada@example.com> <ada@laptop.local>\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "mailmap", author=OTHER)

    # One account email reaches the laptop email through .mailmap, and the name the user's own
    # commits carry reaches the third; the noreply commit needs the login.
    email_only = build(repo, "ada/x", Seeds.make([USER[1]]))
    assert "User commits: 3 of 6" in email_only.text
    wide = build(repo, "ada/x", Seeds.make([USER[1]], names=["Ada Lovelace"], login="ada",
                                           user_id=7))
    assert "User commits: 4 of 6 non-merge commits (67%), 2 author(s)" in wide.text
    who = resolve(snapshot(repo), Seeds.make([USER[1]], names=["Ada Lovelace"]))
    assert who.matches("anyone", "ada@laptop.local") and not who.matches(*OTHER)


# ── determinism: OS checkouts and hash seeds ─────────────────────────────────

def test_a_crlf_checkout_gives_the_same_dossier(two_authors, tmp_path):
    crlf = tmp_path / "crlf"
    subprocess.run(["git", "-c", "core.autocrlf=true", "clone", "-q", str(two_authors), str(crlf)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(crlf), "config", "core.autocrlf", "true"], check=True)
    assert b"\r\n" in (crlf / "pkg" / "core.py").read_bytes()
    a = build(two_authors, "ada/demo", ada())
    b = build(crlf, "ada/demo", ada())
    assert a.text == b.text


def test_committed_crlf_text_is_normalised(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "README.md", "# CRLF\nline two\n", newline="\r\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "crlf", author=USER)
    d = build(repo, "ada/crlf", ada())
    assert "\r" not in d.text and "## README (README.md)\n# CRLF\nline two" in d.text
    assert "README.md  (18B)" in d.text            # the committed bytes, CRs included


def test_a_symlinked_readme_reads_its_target_through_git(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "docs/README.md", "# Real readme\nThe project explained.\n")
    write(repo, "a.py", "def a():\n    return 1\n")
    git(repo, "add", "-A")
    blob = git(repo, "hash-object", "-w", "--stdin", inp=b"docs/README.md")
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},README.md")
    blob2 = git(repo, "hash-object", "-w", "--stdin", inp=b"../outside.md")
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob2},broken.md")
    git(repo, "commit", "-q", "-m", "link", author=USER)
    d = build(repo, "ada/link", ada())
    assert "## README (README.md)\n# Real readme\nThe project explained." in d.text
    tree = d.text[d.text.index("## File tree"):d.text.index("## Authorship")]
    assert "broken.md" not in tree                 # a link out of the repo is dropped
    assert "README.md  (37B)" in d.text            # sized as its target


def test_the_dossier_is_byte_identical_across_hash_seeds(two_authors):
    code = ("import sys; from harness.dossier import Seeds, build; "
            "sys.stdout.write(build(sys.argv[1], 'ada/demo', Seeds.make(['ada@example.com'])).text)")
    outs = set()
    for seed in ("0", "1", "4242"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        done = subprocess.run([sys.executable, "-c", code, str(two_authors)], cwd=ROOT, env=env,
                              capture_output=True, check=True)
        outs.add(done.stdout)
    assert len(outs) == 1


def test_the_port_matches_the_eval_blame_20k_dossier(two_authors):
    """Everything but the metadata block (reworded on purpose: commit dates, no stars) is
    #397's evaluated `blame-20k`, byte for byte."""
    pytest.importorskip("networkx")
    from eval.repo_dossier.ladder import RepoInput
    from eval.repo_dossier.variants import build_variant, make_context
    ctx = make_context(RepoInput(two_authors, "ada/demo", "the user", [USER[1]], None))
    want, _ = build_variant(ctx, "blame-20k")
    got = build(two_authors, "ada/demo", ada(), user_label="the user").text

    def drop_meta(text):
        return re.sub(r"## Metadata\n.*?\n\n## File tree", "## File tree", text, flags=re.S)
    assert drop_meta(got) == drop_meta(want)


# ── the budget ───────────────────────────────────────────────────────────────

def test_a_large_repo_fits_the_20k_cap_with_cut_markers(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "README.md", "# Big\n" + "".join(f"Paragraph {i} about the system.\n"
                                               for i in range(6000)))
    for i in range(120):
        write(repo, f"mod{i:03d}/m.py", "".join(f"def function_{i}_{j}(x):\n    return helper_{j}(x)\n\n\n"
                                               for j in range(25)))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "big", author=USER)
    d = build(repo, "ada/big", ada())
    assert len(d.text) <= BUDGET_TOKENS * CHARS_PER_TOKEN
    assert CUT_MARKER.strip() in d.text
    assert set(d.stats["cut"]) >= {"readme"}
    smap = d.text[d.text.index("## Symbol map"):d.text.index("## Built by other authors")]
    assert "symbols" in d.stats["cut"]
    assert smap.rstrip().splitlines()[-1].startswith("  L")   # re-rendered: whole definitions


def test_fit_gives_unused_share_to_the_sections_that_need_it():
    out, alloc = fit([("readme", "r" * 10), ("symbols", "s\n" * 30_000)], 1_000)
    assert out["readme"] == "r" * 10
    assert alloc["symbols"] == int(1_000 * CHARS_PER_TOKEN * 0.98) - 10
    assert out["symbols"].endswith(CUT_MARKER)


# ── the bounds ───────────────────────────────────────────────────────────────

def test_select_sources_keeps_the_users_files_first_past_the_cap():
    paths = [f"d{i}/f{j}.py" for i in range(5) for j in range(10)]
    got = sym.select_sources(paths, {"d3/f1.py": 5.0, "d0/f0.py": 9.0}, max_files=12)
    assert len(got) == 12 and got == sorted(got)
    assert {"d0/f0.py", "d3/f1.py"} <= set(got)
    assert all(p.startswith(("d0/", "d3/")) for p in got)       # their neighbours next
    assert sym.select_sources(paths[:5], {}, max_files=12) == sorted(paths[:5])


def test_a_name_with_more_than_twenty_definers_adds_no_edges():
    sources = {f"f{i:02d}.py": "def init_component():\n    pass\n"
               for i in range(sym.MAX_DEFINERS + 1)}
    sources["user.py"] = "def run():\n    init_component()\n    special_helper()\n"
    sources["lib.py"] = "def special_helper():\n    pass\n"
    key, run = ("f01.py", "init_component"), ("user.py", "run")
    ranked = sym.rank_sources(sources, {"user.py": 3.0})
    assert ranked.keys[0] == ("lib.py", "special_helper")
    assert ranked.keys.index(key) > ranked.keys.index(run)     # 21 definers: no edge
    sources.pop("f00.py")                                      # 20: edges again
    ranked = sym.rank_sources(sources, {"user.py": 3.0})
    assert ranked.keys.index(key) < ranked.keys.index(run)


def test_blame_is_bounded_by_files_lines_and_introducer_lookups(two_authors):
    snap = snapshot(two_authors)
    who = resolve(snap, ada())
    index = blame_mod.BlameIndex(snap, who, max_files=1, max_lines=9, max_intro=1)
    index.prefetch(["pkg/core.py", "pkg/util.py", "app.ts"])
    assert index.files["pkg/core.py"] is None                  # 11 lines > 9
    assert index.files["pkg/util.py"] is not None              # the one blamed file
    assert "app.ts" not in index.files                         # past the file cap
    assert index.introducer("pkg/util.py", 0, 2) is not None
    assert index.introducer("pkg/util.py", 5, 7) is None       # past the lookup cap


def test_pagerank_matches_networkx():
    nx = pytest.importorskip("networkx")
    pytest.importorskip("scipy")
    g = nx.MultiDiGraph()
    nodes = ["a", "b", "c", "d"]
    g.add_nodes_from(nodes)
    links = {"a": {"b": 2.0, "c": 1.0}, "b": {"c": 1.0}, "c": {"a": 0.5}}
    for u, row in links.items():
        for v, w in row.items():
            g.add_edge(u, v, weight=w)
    pers = {"a": 0.7, "b": 0.3, "c": 0.0, "d": 0.0}
    want = nx.pagerank(g, weight="weight", personalization=pers, dangling=pers)
    got = sym.pagerank(nodes, links, pers)
    assert all(abs(got[n] - want[n]) < 1e-6 for n in nodes)


def test_the_builder_never_fetches_or_imports_a_network_client():
    """The clone and every fetch are `harness/github.py`'s; the builder reads git only."""
    import ast
    for f in (ROOT / "harness" / "dossier").glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not {m.split(".")[0] for m in names} & {"requests", "urllib", "http", "socket",
                                                       "httpx", "harness"} - {"harness"}, f.name
        assert not any(m.startswith("harness.github") for m in names), f.name
        consts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)}
        assert not consts & {"clone", "fetch", "ls-remote", "pull", "push"}, f.name
