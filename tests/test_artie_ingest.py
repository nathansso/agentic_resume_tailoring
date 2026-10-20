"""`artie ingest` and staged sources (#398), offline.

GitHub is a fake client and every "remote" is a fixture repo on disk, cloned for real with
`harness.github.sync_clone`, so the clone, fetch and incremental paths all run without the
network. The dossier's own content is covered in tests/test_dossier.py.
"""

import argparse
import base64
import json
import shutil
import subprocess

import pytest
from sqlmodel import Session, select

from conftest import _seed_user_and_skill
from database.models import StagedSource
from harness import github as gh
from harness import ingest_cmd, sources
from harness.contract import invoke
from test_dossier import OTHER, USER, git, make_repo, write
from test_harness_contract import _via_cli, _via_mcp

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
TOKEN = "ghx_ArtTestTokenNeverLogged0123456789"


class FakeClient:
    """The few `GitHubClient` calls `artie ingest` makes, over fixture repos."""

    def __init__(self, repos, emails=("ada@example.com",), linked=()):
        self.repos, self.emails, self.linked = repos, list(emails), list(linked)
        self.calls = []

    def user(self):
        self.calls.append("user")
        return {"login": "ada", "id": 7, "name": "Ada Lovelace", "scopes": ["read:user"]}

    def account_emails(self):
        self.calls.append("emails")
        return self.emails

    def all_repos(self, include_private=False, limit=1000):
        self.calls.append("repos")
        return [dict(r) for r in self.repos]

    def commit_emails(self, full_name, login, pages=3):
        self.calls.append(f"commits:{full_name}")
        return self.linked


def _row(name, *, fork=False):
    return {"name": name, "full_name": f"ada/{name}", "fork": fork, "default_branch": "main",
            "description": f"The {name} project", "topics": ["ml"], "stargazers_count": 3}


@pytest.fixture
def user(isolated_engine):
    return _seed_user_and_skill(isolated_engine).user_id


@pytest.fixture
def remotes(tmp_path):
    """Two fixture repos standing in for GitHub; `forked` is a fork that is skipped."""
    out = {}
    (tmp_path / "remotes").mkdir()
    for name in ("forecast", "charts", "forked"):
        repo = make_repo(tmp_path / "remotes", name)
        write(repo, "README.md", f"# {name}\n")
        write(repo, "pkg/core.py", "def train(rows):\n    return rows\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "init", author=OTHER)
        write(repo, "pkg/model.py", "from pkg.core import train\n\n\ndef fit(x):\n    return train(x)\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "feat: model", author=USER)
        out[name] = repo
    return out


def _args(*argv):
    return ingest_cmd._parser().parse_args(list(argv))


def _sync(remotes):
    """`sync_clone` for real, with each GitHub URL swapped for its fixture path."""
    def sync(url, dest, *, branch=None, token=None):
        name = url.rsplit("/", 1)[-1][:-len(".git")]
        return gh.sync_clone(str(remotes[name]), dest, branch=branch, token=token)
    return sync


def _ingest(user, remotes, tmp_path, *argv, client=None):
    out = []
    code = ingest_cmd.run(_args("--json", *argv), user_id=user,
                          client=client or FakeClient([_row("forecast"), _row("charts"),
                                                       _row("forked", fork=True)]),
                          token=TOKEN, sync=_sync(remotes), clones=tmp_path / "clones",
                          say=lambda line: None, write=out.append)
    return code, json.loads(out[-1])


def _rows(engine, user):
    with Session(engine) as s:
        rows = s.exec(select(StagedSource).where(StagedSource.user_id == user)).all()
        return {r.key: r.model_dump() for r in rows}


# ── staging, incremental by commit ───────────────────────────────────────────

def test_ingest_stages_one_dossier_per_owned_repo_and_points_at_the_agent(
        user, remotes, tmp_path, isolated_engine):
    code, doc = _ingest(user, remotes, tmp_path)
    assert code == 0 and doc["ok"]
    assert sorted((r["key"], r["action"]) for r in doc["results"]) == [
        ("ada/charts", "staged"), ("ada/forecast", "staged")]          # the fork is left out
    assert doc["counts"] == {"staged": 2, "processed": 0}
    assert doc["next"] == 'Next: ask your agent to "process my new sources".'
    rows = _rows(isolated_engine, user)
    head = git(remotes["forecast"], "rev-parse", "HEAD")
    assert rows["ada/forecast"]["commit"] == head
    assert rows["ada/forecast"]["content"].startswith(
        f"# Repository dossier: ada/forecast at commit {head[:12]}")
    assert "Description: The forecast project" in rows["ada/forecast"]["content"]
    assert (tmp_path / "clones" / "ada" / "forecast.git" / "HEAD").is_file()   # a bare clone


def test_a_second_run_with_no_new_commits_changes_nothing(user, remotes, tmp_path,
                                                          isolated_engine, monkeypatch):
    _ingest(user, remotes, tmp_path)
    before = _rows(isolated_engine, user)
    from harness import dossier

    def no_build(*a, **k):
        raise AssertionError("rebuilt an unchanged repo")
    monkeypatch.setattr(dossier, "build", no_build)
    client = FakeClient([_row("forecast"), _row("charts")])
    code, doc = _ingest(user, remotes, tmp_path, client=client)
    assert code == 0 and {r["action"] for r in doc["results"]} == {"unchanged"}
    assert _rows(isolated_engine, user) == before
    assert not [c for c in client.calls if c.startswith("commits:")]  # no identity lookups


def test_a_new_commit_restages_a_processed_repo(user, remotes, tmp_path, isolated_engine):
    _ingest(user, remotes, tmp_path)
    sid = _rows(isolated_engine, user)["ada/forecast"]["source_id"]
    assert sources.mark_source_processed(user, str(sid), "3 records")["changed"]
    write(remotes["forecast"], "pkg/eval.py", "def score(x):\n    return x\n")
    git(remotes["forecast"], "add", "-A")
    git(remotes["forecast"], "commit", "-q", "-m", "feat: eval", author=USER)
    code, doc = _ingest(user, remotes, tmp_path)
    actions = {r["key"]: r["action"] for r in doc["results"]}
    assert actions == {"ada/forecast": "restaged", "ada/charts": "unchanged"}
    row = _rows(isolated_engine, user)["ada/forecast"]
    assert row["status"] == "staged" and row["note"] is None and row["source_id"] == sid
    assert "pkg/eval.py" in row["content"]


def test_repo_and_limit_pick_repos_and_a_missing_one_fails_alone(user, remotes, tmp_path):
    code, doc = _ingest(user, remotes, tmp_path, "--repo", "charts", "--repo", "ada/nope")
    assert code == 1
    assert {(r["key"], r["action"]) for r in doc["results"]} == {
        ("ada/charts", "staged"), ("ada/nope", "failed")}
    code, doc = _ingest(user, remotes, tmp_path, "--limit", "1")
    assert [r["key"] for r in doc["results"]] == ["ada/forecast"]


def test_identity_includes_the_emails_github_links_to_the_login(user, remotes, tmp_path,
                                                                isolated_engine):
    repo = remotes["charts"]
    write(repo, "pkg/extra.py", "def extra():\n    return 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "from the laptop", author=("nobody", "private@laptop"))
    client = FakeClient([_row("charts")], emails=[], linked=["private@laptop"])
    _ingest(user, remotes, tmp_path, client=client)
    text = _rows(isolated_engine, user)["ada/charts"]["content"]
    assert "User commits: 2 of 3 non-merge commits" in text
    assert "commits:ada/charts" in client.calls


def test_a_local_folder_is_staged_without_github(user, remotes, tmp_path, isolated_engine):
    out = []
    code = ingest_cmd.run(_args("--path", str(remotes["charts"])), user_id=user, client=None,
                          clones=tmp_path / "clones", say=lambda line: None, write=out.append)
    assert code == 0
    assert any("new" in line and "charts" in line for line in out)
    assert out[-1] == 'Next: ask your agent to "process my new sources".'
    row = next(iter(_rows(isolated_engine, user).values()))
    assert row["kind"] == "local_repo" and row["title"] == "charts"
    assert sources.evidence_source(row["kind"], row["key"]) == "repo:charts"


def test_without_a_github_sign_in_ingest_says_how_to_sign_in(user, tmp_path):
    out = []
    code = ingest_cmd.run(_args(), user_id=user, client=None, clones=tmp_path,
                          say=lambda line: None, write=out.append)
    assert code == 1
    assert "Not signed in to GitHub. Run: artie connect" in "\n".join(out)
    assert out[-1] == "Nothing is waiting for your agent."


def test_artie_ingest_dispatches_from_the_entry_point(monkeypatch):
    from harness import entry
    seen = []
    monkeypatch.setattr("harness.ingest_cmd.main", lambda argv: seen.append(argv) or 0)
    assert entry.main(["ingest", "--repo", "x"]) == 0 and seen == [["--repo", "x"]]
    assert "artie ingest" in entry.HELP


# ── the staged-source tools, on every adapter ────────────────────────────────

def test_the_agent_lists_reads_and_marks_sources_on_every_adapter(user, remotes, tmp_path,
                                                                  capsys):
    _ingest(user, remotes, tmp_path)
    listed = invoke("list_staged_sources", user, {})
    assert {s["evidence_source"] for s in listed["sources"]} == {"github:forecast", "github:charts"}
    assert listed["content"] is None and listed["counts"] == {"staged": 2, "processed": 0}
    sid = next(s["source_id"] for s in listed["sources"] if s["key"] == "ada/forecast")
    one = invoke("list_staged_sources", user, {"source_id": sid})
    assert one["content"].startswith("# Repository dossier: ada/forecast")
    for args in ({}, {"source_id": sid}, {"status": None}):
        assert _via_mcp("list_staged_sources", user, args) == invoke("list_staged_sources", user, args)
        assert _via_cli("list_staged_sources", user, args, capsys) == invoke(
            "list_staged_sources", user, args)
    marked = _via_mcp("mark_source_processed", user, {"source_id": sid, "note": "4 records"})
    assert marked["changed"] and marked["sources"][0]["status"] == "processed"
    again = _via_cli("mark_source_processed", user, {"source_id": sid}, capsys)
    assert not again["changed"] and again["sources"][0]["note"] == "4 records"
    assert invoke("list_staged_sources", user, {})["counts"] == {"staged": 1, "processed": 1}
    assert [s["key"] for s in invoke("list_staged_sources", user, {"status": "processed"})[
        "sources"]] == ["ada/forecast"]


def test_one_users_sources_are_not_another_users(user, remotes, tmp_path, isolated_engine):
    _ingest(user, remotes, tmp_path)
    from database.models import User
    with Session(isolated_engine) as s:
        other = User(name="Other", email="o@example.com")
        s.add(other)
        s.commit()
        other_id = other.user_id
    sid = next(iter(_rows(isolated_engine, user).values()))["source_id"]
    assert invoke("list_staged_sources", other_id, {})["sources"] == []
    assert invoke("list_staged_sources", other_id, {"source_id": str(sid)})["error"]["code"] == "not_found"
    assert invoke("mark_source_processed", other_id, {"source_id": str(sid)})["error"]["code"] == "not_found"


# ── the clone keeps the token out of argv, config and errors ─────────────────

def test_sync_clone_passes_the_token_only_through_the_environment(tmp_path):
    seen = []

    class Done:
        returncode, stdout, stderr = 0, "a" * 40 + "\n", ""

    def run(argv, **kw):
        seen.append((argv, kw["env"]))
        return Done()
    sha = gh.sync_clone(gh.clone_url("ada/forecast"), tmp_path / "x.git", branch="main",
                        token=TOKEN, run=run)
    assert sha == "a" * 40
    basic = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    for argv, env in seen:
        assert TOKEN not in " ".join(argv) and basic not in " ".join(argv)
        assert env["GIT_CONFIG_KEY_1"] == "http.https://github.com/.extraheader"
        assert env["GIT_CONFIG_VALUE_1"] == f"AUTHORIZATION: basic {basic}"
        assert env["GIT_CONFIG_KEY_0"] == "credential.helper" and env["GIT_TERMINAL_PROMPT"] == "0"
    assert seen[0][0][:3] == ["git", "clone", "--bare"]


def test_a_failed_clone_never_echoes_the_token(tmp_path):
    basic = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()

    class Fail:
        returncode, stdout = 128, ""
        stderr = f"fatal: header AUTHORIZATION: basic {basic} rejected for {TOKEN}"

    with pytest.raises(gh.GitHubError) as err:
        gh.sync_clone(gh.clone_url("ada/x"), tmp_path / "x.git", token=TOKEN,
                      run=lambda argv, **kw: Fail())
    assert err.value.code == "clone_failed"
    assert TOKEN not in str(err.value) and basic not in str(err.value)


def test_a_clone_gets_no_token_for_a_host_other_than_github(tmp_path, remotes):
    sha = gh.sync_clone(str(remotes["charts"]), tmp_path / "c.git", token=TOKEN)
    assert sha == git(remotes["charts"], "rev-parse", "HEAD")
    config = (tmp_path / "c.git" / "config").read_text()
    assert TOKEN not in config and "extraheader" not in config
    assert gh._git_auth_env(TOKEN, str(remotes["charts"]))["GIT_CONFIG_COUNT"] == "1"


# ── GitHub identity calls on a fake transport ────────────────────────────────

def test_account_and_commit_emails_come_from_the_github_client():
    calls = []

    def transport(method, url, headers, form, timeout):
        calls.append(url)
        if url.endswith("/user"):
            return 200, {}, json.dumps({"login": "ada", "id": 7, "email": "Ada@Example.com"})
        if url.endswith("/user/emails"):
            return 404, {}, json.dumps({"message": "Not Found"})       # read:user token
        if "/repos/ada/x/commits" in url:
            return 200, {}, json.dumps([{"commit": {"author": {"email": "Private@Laptop"}}},
                                        {"commit": {"author": {"email": "ada@example.com"}}}])
        raise AssertionError(url)
    client = gh.GitHubClient(TOKEN, transport=transport)
    assert client.account_emails() == ["ada@example.com"]
    assert client.commit_emails("ada/x", "ada") == ["ada@example.com", "private@laptop"]
    assert any("author=ada" in u for u in calls)
