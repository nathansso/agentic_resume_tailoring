"""`art github login|logout|status` and `list_github_repos` (issue #353).

GitHub is a fake transport throughout: nothing here reaches the network, a keychain or
a real token. The fake speaks the parts of the device flow and REST API the client
uses, and honours `visibility`, `page` and `per_page` the way GitHub does, so the
pagination and private-scope tests exercise the client's own logic.
"""

import asyncio
import json
import logging
import sys
import zipfile
from urllib.parse import parse_qs, urlsplit

import pytest
import sqlalchemy as sa

from harness import github as gh
from harness import github_cli
from harness.contract import BY_NAME, invoke
from harness.github import GitHubClient, GitHubError

# Not shaped like a real token on purpose: the export redactor masks `gh[pousr]_...`, which
# would hide a leak from the export and DB checks below.
TOKEN = "ghx_ArtTestTokenNeverLogged0123456789"
CLIENT_ID = "Iv1.testclientid"


# ── a fake GitHub ────────────────────────────────────────────────────────────

def _repo(i, *, private=False, fork=False, archived=False):
    return {"name": f"repo{i}", "full_name": f"ada/repo{i}", "description": f"Repo number {i}",
            "topics": ["ml", "python"][: i % 3], "stargazers_count": i * 2,
            "pushed_at": f"2026-09-{30 - i:02d}T00:00:00Z", "fork": fork, "archived": archived,
            "private": private, "html_url": f"https://github.com/ada/repo{i}",
            "clone_url": f"https://github.com/ada/repo{i}.git"}


class FakeGitHub:
    """Callable transport. `polls` is the script for the token endpoint, consumed in order."""

    def __init__(self, repos=None, *, polls=None, scopes="read:user", token=TOKEN,
                 interval=5, expires_in=900, readmes=None, user_status=200):
        self.repos = repos if repos is not None else [_repo(i) for i in range(1, 6)]
        self.polls = list(polls if polls is not None else [{"access_token": token,
                                                           "scope": scopes.replace(" ", ",")}])
        self.scopes, self.token = scopes, token
        self.interval, self.expires_in = interval, expires_in
        self.readmes = readmes if readmes is not None else {}
        self.user_status = user_status
        self.calls = []

    def __call__(self, method, url, headers, form, timeout):
        self.calls.append((method, url, dict(headers), dict(form or {})))
        parts = urlsplit(url)
        path, query = parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}
        if method == "POST" and url == gh.DEVICE_CODE_URL:
            return 200, {}, json.dumps({
                "device_code": "DEVICE-SECRET", "user_code": "WDJB-MJHT",
                "verification_uri": "https://github.com/login/device",
                "expires_in": self.expires_in, "interval": self.interval})
        if method == "POST" and url == gh.ACCESS_TOKEN_URL:
            return 200, {}, json.dumps(self.polls.pop(0))
        assert headers.get("Authorization") == f"Bearer {self.token}", "token not sent"
        if path == "/user":
            if self.user_status != 200:
                return self.user_status, {}, json.dumps({"message": "Bad credentials"})
            return 200, {"x-oauth-scopes": self.scopes.replace(" ", ", ")}, json.dumps(
                {"login": "ada", "id": 7, "name": "Ada"})
        if path == "/user/repos":
            rows = [r for r in self.repos if query.get("visibility") == "all" or not r["private"]]
            page, per = int(query.get("page", 1)), int(query.get("per_page", 30))
            chunk = rows[(page - 1) * per: page * per]
            more = len(rows) > page * per
            link = {"link": f'<{url}>; rel="next"'} if more else {}
            return 200, link, json.dumps(chunk)
        if path.endswith("/languages"):
            return 200, {}, json.dumps({"Shell": 10, "Python": 900, "HTML": 90})
        if path.endswith("/readme"):
            name = path.split("/")[-2]
            if name in self.readmes:
                return 200, {}, self.readmes[name]
            return 404, {}, json.dumps({"message": "Not Found"})
        raise AssertionError(f"unexpected request {method} {url}")


class FakeKeyring:
    """`keyring`'s three functions over a dict, with an ordinary backend class."""

    class Backend:
        pass

    def __init__(self):
        self.store = {}

    def get_keyring(self):
        return self.Backend()

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, password):
        self.store[(service, user)] = password

    def delete_password(self, service, user):
        self.store.pop((service, user))


@pytest.fixture()
def keyring(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setitem(sys.modules, "keyring", fake)
    return fake


@pytest.fixture()
def fake_github(monkeypatch):
    """A signed-in session on a fake GitHub, installed as the default transport."""
    fake = FakeGitHub()
    monkeypatch.setattr(gh, "_requests_transport", fake)
    monkeypatch.setenv("ART_GITHUB_TOKEN", TOKEN)
    return fake


def _nosleep(seconds):
    _nosleep.slept.append(seconds)


@pytest.fixture(autouse=True)
def _reset_sleep():
    _nosleep.slept = []


# ── the device flow ──────────────────────────────────────────────────────────

def test_the_device_flow_waits_through_pending_and_slow_down_then_returns_the_token():
    fake = FakeGitHub(polls=[{"error": "authorization_pending"}, {"error": "slow_down", "interval": 10},
                             {"error": "authorization_pending"},
                             {"access_token": TOKEN, "scope": "read:user,repo"}])
    code = gh.request_device_code(gh.scopes_for(True), client=CLIENT_ID, transport=fake)
    assert (code.user_code, code.verification_uri) == ("WDJB-MJHT", "https://github.com/login/device")
    granted = gh.poll_for_token(code, client=CLIENT_ID, transport=fake, sleep=_nosleep,
                                clock=lambda: 0)
    assert granted.token == TOKEN and granted.scopes == ("read:user", "repo")
    assert _nosleep.slept == [5, 5, 10, 10]            # slow_down moved the interval to 10
    first = fake.calls[0]
    assert first[1] == gh.DEVICE_CODE_URL and first[3] == {"client_id": CLIENT_ID,
                                                            "scope": "read:user repo"}
    poll = fake.calls[1]
    assert poll[1] == gh.ACCESS_TOKEN_URL
    assert poll[3]["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
    assert poll[3]["device_code"] == "DEVICE-SECRET" and poll[2]["Accept"] == "application/json"


def test_slow_down_without_an_interval_adds_five_seconds():
    fake = FakeGitHub(polls=[{"error": "slow_down"}, {"access_token": TOKEN, "scope": ""}])
    code = gh.request_device_code(client=CLIENT_ID, transport=fake)
    gh.poll_for_token(code, client=CLIENT_ID, transport=fake, sleep=_nosleep, clock=lambda: 0)
    assert _nosleep.slept == [5, 10]


def test_an_expired_token_error_ends_the_wait():
    fake = FakeGitHub(polls=[{"error": "authorization_pending"}, {"error": "expired_token"}])
    code = gh.request_device_code(client=CLIENT_ID, transport=fake)
    with pytest.raises(GitHubError) as err:
        gh.poll_for_token(code, client=CLIENT_ID, transport=fake, sleep=_nosleep, clock=lambda: 0)
    assert err.value.code == "expired_token" and "Run the login again" in err.value.message


def test_a_code_past_its_deadline_expires_without_another_poll():
    fake = FakeGitHub(expires_in=60, polls=[])
    code = gh.request_device_code(client=CLIENT_ID, transport=fake)
    ticks = iter([0, 61])
    with pytest.raises(GitHubError) as err:
        gh.poll_for_token(code, client=CLIENT_ID, transport=fake, sleep=_nosleep,
                          clock=lambda: next(ticks))
    assert err.value.code == "expired_token"
    assert [c[1] for c in fake.calls] == [gh.DEVICE_CODE_URL]      # no poll after the deadline


def test_a_declined_authorisation_and_a_disabled_device_flow_are_named():
    fake = FakeGitHub(polls=[{"error": "access_denied"}])
    code = gh.request_device_code(client=CLIENT_ID, transport=fake)
    with pytest.raises(GitHubError) as err:
        gh.poll_for_token(code, client=CLIENT_ID, transport=fake, sleep=_nosleep, clock=lambda: 0)
    assert err.value.code == "access_denied"

    def disabled(*a):
        return 200, {}, json.dumps({"error": "device_flow_disabled"})
    with pytest.raises(GitHubError) as err:
        gh.request_device_code(client=CLIENT_ID, transport=disabled)
    assert err.value.code == "device_flow_disabled" and "Enable Device Flow" in err.value.message


def test_with_no_client_id_login_names_the_owner_step(monkeypatch, capsys, keyring):
    assert gh.client_id() == ""
    with pytest.raises(GitHubError) as err:
        gh.request_device_code()
    assert err.value.code == "not_configured"
    assert "OAuth App" in err.value.message and "device flow" in err.value.message
    assert github_cli.login() == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["error"]["code"] == "not_configured"
    monkeypatch.setenv("ART_GITHUB_CLIENT_ID", f"  {CLIENT_ID} ")
    assert gh.client_id() == CLIENT_ID


# ── login, status and logout through the CLI ─────────────────────────────────

def test_login_stores_the_token_in_the_keychain_and_prints_no_secret(keyring, capsys):
    fake = FakeGitHub(scopes="read:user repo")
    said = []
    code = github_cli.login(private=True, client_id=CLIENT_ID, transport=fake, sleep=_nosleep,
                            clock=lambda: 0, say=said.append)
    out = capsys.readouterr()
    assert code == 0
    assert keyring.store == {("art-mcp", "github-token"): TOKEN}
    doc = json.loads(out.out)
    assert doc == {"ok": True, "login": "ada", "scopes": ["read:user", "repo"],
                   "private_scope": True, "stored": "keychain"}
    assert "WDJB-MJHT" in said[0] and "https://github.com/login/device" in said[0]
    assert fake.calls[0][3]["scope"] == "read:user repo"
    assert TOKEN not in out.out + out.err + " ".join(said) and "DEVICE-SECRET" not in out.out


def test_login_asks_only_for_read_user_unless_private_is_given(keyring, capsys):
    fake = FakeGitHub()
    github_cli.login(client_id=CLIENT_ID, transport=fake, sleep=_nosleep, clock=lambda: 0,
                     say=lambda line: None)
    assert fake.calls[0][3]["scope"] == "read:user"
    assert json.loads(capsys.readouterr().out)["private_scope"] is False


def test_login_without_a_keychain_stops_before_starting_the_flow(capsys):
    fake = FakeGitHub()
    assert github_cli.login(client_id=CLIENT_ID, transport=fake) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["error"]["code"] == "no_keychain" and "ART_GITHUB_TOKEN" in doc["error"]["message"]
    assert fake.calls == []


def test_a_null_keyring_backend_does_not_count_as_a_keychain(monkeypatch):
    fake = FakeKeyring()
    fail = type("Keyring", (), {"__module__": "keyring.backends.fail"})
    fake.get_keyring = lambda: fail()
    monkeypatch.setitem(sys.modules, "keyring", fake)
    assert gh.keychain_available() is False


def test_a_refused_or_expired_login_exits_one_and_stores_nothing(keyring, capsys):
    fake = FakeGitHub(polls=[{"error": "access_denied"}])
    assert github_cli.login(client_id=CLIENT_ID, transport=fake, sleep=_nosleep,
                            clock=lambda: 0, say=lambda line: None) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "access_denied"
    assert keyring.store == {}


def test_status_reports_the_login_and_scopes_never_the_token(keyring, monkeypatch, capsys):
    monkeypatch.setattr(gh, "_requests_transport", FakeGitHub(scopes="read:user repo"))
    out = gh.login_status()
    assert out["logged_in"] is False and out["source"] is None
    keyring.set_password("art-mcp", "github-token", TOKEN)
    assert github_cli.status() == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["logged_in"] is True and doc["login"] == "ada" and doc["source"] == "keychain"
    assert doc["private_scope"] is True and doc["keychain"] is True
    assert TOKEN not in json.dumps(doc)


def test_status_with_a_revoked_token_says_so(keyring, monkeypatch):
    monkeypatch.setattr(gh, "_requests_transport", FakeGitHub(user_status=401))
    keyring.set_password("art-mcp", "github-token", TOKEN)
    out = gh.login_status()
    assert out["logged_in"] is False and out["error"]["code"] == "bad_token"


def test_logout_removes_the_keychain_entry(keyring, capsys):
    keyring.set_password("art-mcp", "github-token", TOKEN)
    assert github_cli.logout() == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["removed"] is True and keyring.store == {} and gh.resolve_token() is None
    assert github_cli.logout() == 0
    assert json.loads(capsys.readouterr().out)["removed"] is False


def test_the_environment_token_wins_and_logout_says_to_unset_it(keyring, monkeypatch, capsys):
    keyring.set_password("art-mcp", "github-token", "from-keychain")
    monkeypatch.setenv("ART_GITHUB_TOKEN", " from-env ")
    assert (gh.resolve_token(), gh.token_source()) == ("from-env", "env")
    github_cli.logout()
    doc = json.loads(capsys.readouterr().out)
    assert doc["still_set_in_env"] is True and "unset it" in doc["note"]
    monkeypatch.delenv("ART_GITHUB_TOKEN")
    assert gh.resolve_token() is None            # logout cleared the keychain entry too


def test_art_github_dispatches_from_the_entry_point(keyring, capsys):
    from harness.entry import main
    assert main(["github", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["logged_in"] is False


# ── list_github_repos ────────────────────────────────────────────────────────

def test_repos_come_back_with_every_documented_field(fake_github):
    fake_github.readmes = {"repo2": "# Repo 2\n\nA recommender.\r\n"}
    out = invoke("list_github_repos", None, {"per_page": 3})
    assert out["error"] is None
    assert (out["login"], out["scopes"], out["private_included"]) == ("ada", ["read:user"], False)
    assert [r["name"] for r in out["repos"]] == ["repo1", "repo2", "repo3"]
    r = out["repos"][1]
    assert r == {
        "name": "repo2", "full_name": "ada/repo2", "description": "Repo number 2",
        "languages": ["Python", "HTML", "Shell"], "topics": ["ml", "python"], "stargazers": 4,
        "pushed_at": "2026-09-28T00:00:00Z", "fork": False, "archived": False, "private": False,
        "html_url": "https://github.com/ada/repo2", "clone_url": "https://github.com/ada/repo2.git",
        "clone_command": "git clone https://github.com/ada/repo2.git",
        "evidence_source": "github:repo2", "readme_excerpt": "# Repo 2\n\nA recommender.",
        "readme_truncated": False}
    assert out["repos"][0]["readme_excerpt"] is None             # no README: 404, not an error


def test_fork_and_archived_flags_pass_through(fake_github):
    fake_github.repos = [_repo(1, fork=True), _repo(2, archived=True)]
    rows = invoke("list_github_repos", None, {})["repos"]
    assert [(r["fork"], r["archived"]) for r in rows] == [(True, False), (False, True)]


def test_a_long_readme_is_cut_to_a_few_kb_and_marked(fake_github):
    fake_github.readmes = {"repo1": "x" * 10_000}
    r = invoke("list_github_repos", None, {"per_page": 1})["repos"][0]
    assert len(r["readme_excerpt"]) == gh.README_MAX_CHARS and r["readme_truncated"] is True


def test_include_readme_false_skips_the_readme_requests(fake_github):
    invoke("list_github_repos", None, {"per_page": 2, "include_readme": False})
    assert not [c for c in fake_github.calls if c[1].endswith("/readme")]


def test_the_listing_paginates_with_next_page(fake_github):
    fake_github.repos = [_repo(i) for i in range(1, 6)]
    seen, page = [], 1
    while page:
        out = invoke("list_github_repos", None, {"page": page, "per_page": 2, "include_readme": False})
        seen += [r["name"] for r in out["repos"]]
        assert out["page"] == page and out["per_page"] == 2
        page = out["next_page"]
    assert seen == ["repo1", "repo2", "repo3", "repo4", "repo5"]
    queries = [parse_qs(urlsplit(c[1]).query) for c in fake_github.calls
               if urlsplit(c[1]).path == "/user/repos"]
    assert [q["page"] for q in queries] == [["1"], ["2"], ["3"]]
    assert queries[0]["sort"] == ["pushed"] and queries[0]["affiliation"] == ["owner"]


def test_per_page_is_bounded_by_the_contract(fake_github):
    with pytest.raises(Exception):
        invoke("list_github_repos", None, {"per_page": 500})
    with pytest.raises(Exception):
        invoke("list_github_repos", None, {"page": 0})


def test_private_repos_need_the_repo_scope(fake_github):
    fake_github.repos = [_repo(1), _repo(2, private=True), _repo(3)]
    out = invoke("list_github_repos", None, {})
    assert [r["name"] for r in out["repos"]] == ["repo1", "repo3"]
    assert out["private_included"] is False and "art github login --private" in out["note"]
    assert {parse_qs(urlsplit(c[1]).query)["visibility"][0]
            for c in fake_github.calls if urlsplit(c[1]).path == "/user/repos"} == {"public"}


def test_private_repos_are_listed_when_the_token_holds_repo(fake_github):
    fake_github.scopes = "read:user repo"
    fake_github.repos = [_repo(1), _repo(2, private=True)]
    out = invoke("list_github_repos", None, {})
    assert [(r["name"], r["private"]) for r in out["repos"]] == [("repo1", False), ("repo2", True)]
    assert out["private_included"] is True and out["note"] is None
    out = invoke("list_github_repos", None, {"include_private": False})
    assert [r["name"] for r in out["repos"]] == ["repo1"] and out["private_included"] is False


def test_a_private_repo_the_server_returns_anyway_is_dropped_without_the_scope(
        fake_github, monkeypatch):
    """Defence in depth: the scope decides, not the server's filtering."""
    fake_github.repos = [_repo(1), _repo(2, private=True)]

    def ignores_visibility(method, url, headers, form, timeout):
        return fake_github(method, url.replace("visibility=public", "visibility=all"),
                           headers, form, timeout)
    monkeypatch.setattr(gh, "_requests_transport", ignores_visibility)
    out = invoke("list_github_repos", None, {})
    assert [r["name"] for r in out["repos"]] == ["repo1"]


def test_signed_out_the_tool_says_to_log_in_and_names_no_token(capsys):
    out = invoke("list_github_repos", None, {})
    assert out["error"]["code"] == "not_logged_in" and "art github login" in out["error"]["message"]
    assert out["repos"] == []


def test_a_revoked_token_and_a_used_up_rate_limit_are_errors_not_exceptions(fake_github, monkeypatch):
    fake_github.user_status = 401
    assert invoke("list_github_repos", None, {})["error"]["code"] == "bad_token"

    def limited(method, url, headers, form, timeout):
        return 403, {"x-ratelimit-remaining": "0"}, json.dumps({"message": "rate limit"})
    monkeypatch.setattr(gh, "_requests_transport", limited)
    assert invoke("list_github_repos", None, {})["error"]["code"] == "rate_limited"


def test_the_tool_is_identical_on_every_adapter(fake_github, capsys):
    from harness import cli
    from harness.mcp_server import build_server

    args = {"per_page": 3, "page": 1}
    direct = invoke("list_github_repos", None, args)
    BY_NAME["list_github_repos"].output_model.model_validate(direct)
    via_mcp = asyncio.run(build_server(None).call_tool("list_github_repos", args))
    assert not via_mcp.is_error and via_mcp.structured_content == direct
    capsys.readouterr()
    assert cli.run(["list_github_repos", "--args", json.dumps(args)], user_id=None) == 0
    assert json.loads(capsys.readouterr().out) == direct
    assert direct["repos"] and direct["next_page"] == 2


def test_the_tool_is_a_read_only_network_tool_that_needs_no_profile():
    spec = BY_NAME["list_github_repos"]
    assert spec.read_only and spec.network and not spec.needs_user
    assert spec.output_model.model_fields["repos"] is not None


# ── the token never lands anywhere it should not ─────────────────────────────

def test_the_token_is_in_no_repr_log_result_or_error(keyring, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    fake = FakeGitHub()
    code = gh.request_device_code(client=CLIENT_ID, transport=fake)
    granted = gh.poll_for_token(code, client=CLIENT_ID, transport=fake, sleep=_nosleep,
                                clock=lambda: 0)
    client = GitHubClient(granted.token, transport=fake)
    out = gh.list_github_repos(client=client)
    status = gh.login_status()
    bad = GitHubClient(granted.token, transport=FakeGitHub(user_status=401))
    err = gh.list_github_repos(client=bad)
    with pytest.raises(GitHubError) as raised:
        bad.user()
    surfaces = [repr(code), repr(granted), repr(client), caplog.text, json.dumps(out),
                json.dumps(status), json.dumps(err), str(raised.value), capsys.readouterr().out]
    for text in surfaces:
        assert TOKEN not in text and "DEVICE-SECRET" not in text


def test_the_token_never_reaches_the_database(isolated_engine, kg, keyring, capsys):
    github_cli.login(client_id=CLIENT_ID, transport=FakeGitHub(), sleep=_nosleep,
                     clock=lambda: 0, say=lambda line: None)
    assert keyring.store[("art-mcp", "github-token")] == TOKEN
    with isolated_engine.connect() as conn:
        dump = []
        for table in sa.inspect(isolated_engine).get_table_names():
            dump.append(repr(conn.execute(sa.text(f'SELECT * FROM "{table}"')).fetchall()))
    assert TOKEN not in "\n".join(dump)


def test_the_token_never_reaches_an_export_bundle(isolated_engine, kg, keyring, tmp_path, monkeypatch):
    from harness import export_import as ei

    monkeypatch.setenv("ART_GITHUB_TOKEN", TOKEN)
    keyring.set_password("art-mcp", "github-token", TOKEN)
    out = tmp_path / "bundle.zip"
    ei.export_bundle(isolated_engine, kg, out, apps_dir=tmp_path / "apps", include_cache=True)
    with zipfile.ZipFile(out) as z:
        blob = b"".join(z.read(n) for n in z.namelist())
    assert TOKEN.encode() not in blob
    # ...and the exporter has no way to ask for it: it does not import the GitHub client.
    with open(ei.__file__, encoding="utf-8") as f:
        text = f.read()
    assert "harness.github" not in text and "from harness import github" not in text


def test_the_stub_keyring_is_what_the_suite_sees_by_default():
    """The autouse fixture hides the developer's keychain from every other test."""
    assert sys.modules.get("keyring") is None
    assert gh.resolve_token() is None and gh.keychain_available() is False


def test_login_hints_give_the_pinned_uvx_command_not_a_bare_art():
    """A plugin install puts no `art` on PATH, so every login hint the user or agent sees is the
    pinned uvx form (#369): the tool's not_logged_in error and the plugin's own docs."""
    import re
    from pathlib import Path
    from harness.github import login_command
    from harness.hooks import SOURCE
    assert login_command() == f'uvx --from "{SOURCE}" art github login'
    assert login_command(private=True).endswith("art github login --private")

    plugin = Path(__file__).resolve().parent.parent / "plugin"
    for doc in [plugin / "README.md", plugin / "commands" / "setup.md",
                plugin / "skills" / "art-setup" / "SKILL.md",
                plugin / "skills" / "art-tailor" / "SKILL.md"]:
        text = doc.read_text(encoding="utf-8")
        assert login_command() in text, doc
        assert not re.search(r"run `art github login`", text), doc
