"""`artie connect`: GitHub sign-in and the LinkedIn URL in one step (issue #400).

GitHub is the fake from `test_github.py` (a device flow and a REST API in a callable
transport) over a fake keychain, so nothing reaches the network, a keychain or a real
token. The store is the suite's isolated engine, so the LinkedIn URL really is saved to
(and read back from) a profile.
"""

import json
import re
import sys
from pathlib import Path

import pytest

from harness import connect, entry
from harness import github as gh
from harness.contract import invoke
from harness.linkedin_url import InvalidLinkedInURL, normalize_profile_url
from test_github import TOKEN, FakeGitHub, FakeKeyring, keyring  # noqa: F401  (a fixture)

ROOT = Path(__file__).resolve().parent.parent
URL = "https://www.linkedin.com/in/ada-lovelace"


def _nosleep(seconds):
    pass


class Run:
    """One `artie connect` run: what it printed, said to stderr and returned."""

    def __init__(self, argv, *, uid=None, writes=True, fake=None, answers=None, monkeypatch=None):
        args = connect._parser().parse_args(argv)
        self.out, self.said, self.asked = [], [], []
        queue = list(answers or [])

        def ask(prompt):
            self.asked.append(prompt)
            if not queue:
                raise AssertionError(f"asked {prompt!r} with no answer left")
            return queue.pop(0)

        self.fake = fake if fake is not None else FakeGitHub()
        if monkeypatch is not None:
            monkeypatch.setattr(gh, "_requests_transport", self.fake)   # login_status's path
        self.code = connect.run(args, user_id=uid, allow_writes=writes,
                                interactive=answers is not None, transport=self.fake,
                                sleep=_nosleep, clock=lambda: 0, say=self.said.append, ask=ask,
                                write=self.out.append)

    @property
    def text(self):
        return "\n".join(self.out)

    @property
    def doc(self):
        return json.loads(self.out[-1])


def _stored(uid):
    return invoke("get_profile", uid, {})["linkedin_url"]


# ── GitHub only, LinkedIn only, both ─────────────────────────────────────────

def test_github_only_signs_in_and_never_touches_the_store(keyring, monkeypatch):  # noqa: F811
    run = Run(["--github-only"], monkeypatch=monkeypatch)
    assert run.code == 0
    assert keyring.store == {("art-mcp", "github-token"): TOKEN}
    assert "Signed in to GitHub as ada." in run.text
    assert "  LinkedIn " not in run.text
    assert any("WDJB-MJHT" in line for line in run.said)          # the code goes to stderr
    assert run.fake.calls[0][3]["scope"] == "read:user"


def test_private_asks_for_the_repo_scope(keyring, monkeypatch):  # noqa: F811
    run = Run(["--github-only", "--private"], fake=FakeGitHub(scopes="read:user repo"),
              monkeypatch=monkeypatch)
    assert run.fake.calls[0][3]["scope"] == "read:user repo"
    assert "private and public repos" in run.text


def test_linkedin_only_saves_the_normalised_url_and_never_asks_github(kg, keyring, monkeypatch):  # noqa: F811
    run = Run(["--linkedin-only", "--linkedin-url", "linkedin.com/in/Ada-Lovelace/"], uid=kg,
              monkeypatch=monkeypatch)
    assert run.code == 0
    assert _stored(kg) == URL
    assert run.fake.calls == [] and keyring.store == {}
    assert f"Saved your LinkedIn URL: {URL}" in run.text and "  GitHub " not in run.text


def test_both_steps_ask_for_the_url_after_signing_in(kg, keyring, monkeypatch):  # noqa: F811
    run = Run([], uid=kg, answers=["https://www.linkedin.com/in/ada-lovelace/?trk=x".split("?")[0]],
              monkeypatch=monkeypatch)
    assert run.code == 0 and _stored(kg) == URL
    assert keyring.store == {("art-mcp", "github-token"): TOKEN}
    assert len(run.asked) == 1
    assert run.text.index("Signed in to GitHub") < run.text.index("Saved your LinkedIn URL")
    assert "  GitHub    ada (read:user; public repos)" in run.text
    assert f"  LinkedIn  {URL}" in run.text
    assert "Next:" in run.text


def test_enter_skips_the_question_and_saves_nothing(kg, keyring, monkeypatch):  # noqa: F811
    run = Run(["--linkedin-only"], uid=kg, answers=[""], monkeypatch=monkeypatch)
    assert run.code == 0 and _stored(kg) is None
    assert "LinkedIn skipped." in run.text and "  LinkedIn  no URL saved" in run.text


def test_without_a_terminal_it_does_not_ask(kg, keyring, monkeypatch):  # noqa: F811
    run = Run(["--linkedin-only"], uid=kg, answers=None, monkeypatch=monkeypatch)
    assert run.code == 0 and run.asked == [] and _stored(kg) is None
    assert "--linkedin-url" in run.text


def test_a_bad_answer_is_asked_again_and_three_bad_ones_fail(kg, keyring, monkeypatch, capsys):  # noqa: F811
    bad = Run(["--linkedin-only"], uid=kg, answers=["a", "b", "c"], monkeypatch=monkeypatch)
    assert bad.code == 1 and "No valid LinkedIn URL" in bad.text and _stored(kg) is None
    capsys.readouterr()
    run = Run(["--linkedin-only"], uid=kg, answers=["not a url", "https://example.com/in/x",
                                                     "linkedin.com/in/ada-lovelace"],
              monkeypatch=monkeypatch)
    assert run.code == 0 and _stored(kg) == URL and len(run.asked) == 3
    assert capsys.readouterr().err.count("not a LinkedIn profile URL") == 2


def test_a_bad_url_flag_is_an_error_and_saves_nothing(kg, keyring, monkeypatch):  # noqa: F811
    run = Run(["--linkedin-only", "--linkedin-url", "https://linkedin.com/company/acme"], uid=kg,
              monkeypatch=monkeypatch)
    assert run.code == 1 and _stored(kg) is None
    assert "Not a LinkedIn profile URL" in run.text


def test_a_refused_github_login_is_a_failure_but_linkedin_still_runs(kg, keyring, monkeypatch):  # noqa: F811
    run = Run(["--linkedin-url", URL], uid=kg, fake=FakeGitHub(polls=[{"error": "access_denied"}]),
              monkeypatch=monkeypatch)
    assert run.code == 1 and keyring.store == {} and _stored(kg) == URL
    assert "GitHub sign-in failed (access_denied)" in run.text
    assert "  GitHub    not signed in" in run.text


def test_json_prints_one_document(kg, keyring, monkeypatch):  # noqa: F811
    run = Run(["--json", "--linkedin-url", URL], uid=kg, monkeypatch=monkeypatch)
    assert len(run.out) == 1
    doc = run.doc
    assert doc["ok"] is True and doc["github"]["login"] == "ada"
    assert doc["github"]["action"] == "signed_in" and doc["linkedin"] == {
        "action": "saved", "connected": True, "replaced": None, "url": URL}
    assert "token" not in json.dumps(doc).lower()


# ── running it again ─────────────────────────────────────────────────────────

def test_a_second_run_shows_the_state_and_changes_nothing(kg, keyring, monkeypatch):  # noqa: F811
    first = Run([], uid=kg, answers=[URL], monkeypatch=monkeypatch)
    assert first.code == 0
    stored = dict(keyring.store)
    second = Run([], uid=kg, answers=[], fake=FakeGitHub(), monkeypatch=monkeypatch)
    assert second.code == 0 and second.asked == []              # nothing asked
    assert keyring.store == stored                             # no new token
    assert [c[1] for c in second.fake.calls] == ["https://api.github.com/user"]   # only a status check
    assert "Already signed in to GitHub as ada; nothing changed" in second.text
    assert f"Your LinkedIn URL is {URL}; nothing changed" in second.text
    assert _stored(kg) == URL
    assert "  GitHub    ada" in second.text and f"  LinkedIn  {URL}" in second.text


def test_the_same_url_again_is_not_written(kg, keyring, monkeypatch):  # noqa: F811
    Run(["--linkedin-only", "--linkedin-url", URL], uid=kg, monkeypatch=monkeypatch)
    monkeypatch.setattr(connect, "_save_url", lambda *a: pytest.fail("saved an unchanged URL"))
    again = Run(["--linkedin-only", "--linkedin-url", "linkedin.com/in/ADA-LOVELACE/"], uid=kg,
                monkeypatch=monkeypatch)
    assert again.code == 0 and "nothing changed" in again.text


def test_a_new_url_flag_replaces_the_saved_one(kg, keyring, monkeypatch):  # noqa: F811
    Run(["--linkedin-only", "--linkedin-url", URL], uid=kg, monkeypatch=monkeypatch)
    run = Run(["--linkedin-only", "--linkedin-url", "https://www.linkedin.com/in/ada-b"], uid=kg,
              monkeypatch=monkeypatch)
    assert _stored(kg) == "https://www.linkedin.com/in/ada-b"
    assert "Saved your LinkedIn URL" in run.text


def test_reauth_signs_in_again(keyring, monkeypatch):  # noqa: F811
    keyring.set_password("art-mcp", "github-token", "old-token")
    old = FakeGitHub(token="old-token")
    kept = Run(["--github-only"], fake=old, monkeypatch=monkeypatch)
    assert "Already signed in" in kept.text and keyring.store[("art-mcp", "github-token")] == "old-token"
    fresh = FakeGitHub(token=TOKEN)
    # The new sign-in's /user call carries the new token; the status check carries the old one.
    status = FakeGitHub(token="old-token")

    def transport(method, url, headers, form, timeout):
        return (fresh if headers.get("Authorization") == f"Bearer {TOKEN}" or form else status)(
            method, url, headers, form, timeout)

    monkeypatch.setattr(gh, "_requests_transport", transport)
    args = connect._parser().parse_args(["--github-only", "--reauth"])
    out = []
    code = connect.run(args, transport=transport, sleep=_nosleep, clock=lambda: 0,
                       say=lambda line: None, write=out.append)
    assert code == 0 and "Signed in to GitHub as ada." in "\n".join(out)
    assert keyring.store[("art-mcp", "github-token")] == TOKEN


def test_private_on_a_public_only_sign_in_asks_again_but_a_private_one_stays(keyring, monkeypatch):  # noqa: F811
    keyring.set_password("art-mcp", "github-token", TOKEN)
    fake = FakeGitHub(scopes="read:user repo")
    held = Run(["--github-only", "--private"], fake=fake, monkeypatch=monkeypatch)
    assert "Already signed in" in held.text
    assert [c[1] for c in held.fake.calls] == ["https://api.github.com/user"]

    public = FakeGitHub(scopes="read:user")
    run = Run(["--github-only", "--private"], fake=public, monkeypatch=monkeypatch)
    assert any(c[1] == gh.DEVICE_CODE_URL and c[3]["scope"] == "read:user repo" for c in run.fake.calls)


def test_a_token_in_the_environment_cannot_be_replaced_and_says_so(keyring, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ART_GITHUB_TOKEN", TOKEN)
    run = Run(["--github-only", "--reauth"], monkeypatch=monkeypatch)
    assert run.code == 0 and keyring.store == {}
    assert "ART_GITHUB_TOKEN" in run.text and "Unset it" in run.text
    assert not any(c[1] == gh.DEVICE_CODE_URL for c in run.fake.calls)


def test_a_revoked_token_is_signed_in_again(keyring, monkeypatch):  # noqa: F811
    keyring.set_password("art-mcp", "github-token", "revoked")
    stale = FakeGitHub(token="revoked", user_status=401)
    fresh = FakeGitHub()

    def transport(method, url, headers, form, timeout):
        return (stale if headers.get("Authorization") == "Bearer revoked" else fresh)(
            method, url, headers, form, timeout)

    monkeypatch.setattr(gh, "_requests_transport", transport)
    args = connect._parser().parse_args(["--github-only"])
    out = []
    assert connect.run(args, transport=transport, sleep=_nosleep, clock=lambda: 0,
                       say=lambda line: None, write=out.append) == 0
    assert keyring.store[("art-mcp", "github-token")] == TOKEN


# ── a store that cannot be written ───────────────────────────────────────────

def test_a_read_only_store_refuses_the_url_with_a_clear_message(kg, keyring, monkeypatch):  # noqa: F811
    run = Run(["--linkedin-only", "--linkedin-url", URL], uid=kg, writes=False,
              monkeypatch=monkeypatch)
    assert run.code == 1 and _stored(kg) is None
    assert "read-only" in run.text and "--allow-writes" in run.text and "not saved" in run.text


def test_a_read_only_store_still_shows_a_saved_url(kg, keyring, monkeypatch):  # noqa: F811
    Run(["--linkedin-only", "--linkedin-url", URL], uid=kg, monkeypatch=monkeypatch)
    run = Run(["--linkedin-only"], uid=kg, writes=False, answers=[], monkeypatch=monkeypatch)
    assert run.code == 0 and URL in run.text


def test_no_profile_bound_is_reported_not_raised(keyring, monkeypatch):  # noqa: F811
    run = Run(["--linkedin-only", "--linkedin-url", URL], uid=None, monkeypatch=monkeypatch)
    assert run.code == 1 and "LinkedIn not saved" in run.text


def test_main_binds_the_store_only_for_the_linkedin_step(monkeypatch, kg, keyring):  # noqa: F811
    bound = []

    def bootstrap(database_url, user_id, allow_writes, prepare):
        bound.append((database_url, user_id, allow_writes, prepare))
        return kg, False

    monkeypatch.setattr("harness.runtime.bootstrap", bootstrap)
    monkeypatch.setattr(gh, "_requests_transport", FakeGitHub())
    code = connect.main(["--linkedin-only", "--linkedin-url", URL, "--database-url", "x",
                         "--allow-writes"])
    assert bound == [("x", None, True, True)] and code == 1       # bootstrap said read-only
    bound.clear()
    monkeypatch.setattr(gh, "_requests_transport", FakeGitHub(scopes="read:user"))
    monkeypatch.setattr(connect.github_cli, "sign_in", lambda *a, **k: {
        "ok": True, "login": "ada", "scopes": ["read:user"], "private_scope": False,
        "stored": "keychain"})
    assert connect.main(["--github-only"]) == 0 and bound == []
    with pytest.raises(SystemExit) as err:
        connect.main(["--github-only", "--linkedin-only"])
    assert err.value.code == 2
    with pytest.raises(SystemExit):
        connect.main(["--github-only", "--linkedin-url", URL])


# ── the URL rule ─────────────────────────────────────────────────────────────

GOOD = {
    "https://www.linkedin.com/in/jane-example": "https://www.linkedin.com/in/jane-example",
    "https://www.linkedin.com/in/jane-example/": "https://www.linkedin.com/in/jane-example",
    "http://linkedin.com/in/Jane-Example": "https://www.linkedin.com/in/jane-example",
    "linkedin.com/in/john-q-public-123/": "https://www.linkedin.com/in/john-q-public-123",
    "  www.linkedin.com/in/ab  ": "https://www.linkedin.com/in/ab",
    "https://uk.linkedin.com/in/jane": "https://www.linkedin.com/in/jane",
    "https://www.linkedin.com/in/%C3%A9mile-z": "https://www.linkedin.com/in/%c3%a9mile-z",
}
BAD = ["", "   ", None, 42, "jane-example", "@jane", "https://example.com/in/jane",
       "https://www.linkedin.com/company/acme", "https://www.linkedin.com/in/",
       "https://www.linkedin.com/in/a", "https://www.linkedin.com/in/jane/details",
       "https://www.linkedin.com/pub/jane/1/2/3", "https://evillinkedin.com/in/jane",
       "https://linkedin.com.evil.com/in/jane", "ftp://www.linkedin.com/in/jane",
       "https://user:pw@www.linkedin.com/in/jane", "https://www.linkedin.com:8080/in/jane",
       "https://www.linkedin.com/in/jane example", "https://www.linkedin.com/in/" + "a" * 101,
       "https://www.linkedin.com/in/jane" + "x" * 300, "https://[::1/in/jane"]


@pytest.mark.parametrize("raw,expected", sorted(GOOD.items()))
def test_good_urls_are_normalised(raw, expected):
    assert normalize_profile_url(raw) == expected


@pytest.mark.parametrize("raw", BAD)
def test_bad_urls_are_refused(raw):
    with pytest.raises(InvalidLinkedInURL):
        normalize_profile_url(raw)


def test_the_rule_is_the_gateways(monkeypatch):
    """The harness cannot import the gateway (it deploys alone), so the rule is written out
    twice; this runs one corpus through both and fails if they ever part."""
    monkeypatch.syspath_prepend(str(ROOT / "gateway"))
    from artie_gateway.errors import GatewayError
    from artie_gateway.urls import normalize_profile_url as gateway

    for raw in [*GOOD, *BAD]:
        try:
            theirs = gateway(raw)
        except GatewayError:
            theirs = None
        try:
            ours = normalize_profile_url(raw)
        except InvalidLinkedInURL:
            ours = None
        assert ours == theirs, raw


# ── the token is nowhere ─────────────────────────────────────────────────────

def test_the_token_is_in_no_output_and_not_in_the_database(kg, isolated_engine, keyring,  # noqa: F811
                                                           monkeypatch, capsys):
    import sqlalchemy as sa

    run = Run([], uid=kg, answers=[URL], monkeypatch=monkeypatch)
    again = Run(["--json"], uid=kg, answers=[], monkeypatch=monkeypatch)
    seen = capsys.readouterr()
    surfaces = [run.text, again.text, " ".join(run.said), " ".join(again.said), seen.out, seen.err]
    for text in surfaces:
        assert TOKEN not in text and "DEVICE-SECRET" not in text
    with isolated_engine.connect() as conn:
        dump = "\n".join(repr(conn.execute(sa.text(f'SELECT * FROM "{t}"')).fetchall())
                         for t in sa.inspect(isolated_engine).get_table_names())
    assert TOKEN not in dump and keyring.store[("art-mcp", "github-token")] == TOKEN


# ── what to do next ──────────────────────────────────────────────────────────

def test_the_next_step_does_not_promise_ingest_before_it_exists():
    from harness.contract import BY_NAME
    assert "ingest_linkedin" not in BY_NAME                     # revisit when #355 ships
    for github, url in ((True, URL), (True, None), (False, URL), (False, None)):
        text = connect.next_step(github, url)
        assert "pull my LinkedIn" not in text
        if url:
            assert "not available yet" in text


def test_the_next_step_names_the_agent_request_once_ingest_linkedin_exists():
    assert connect.next_step(True, URL, ingest_linkedin=True) == \
        'Ask your agent to "pull my LinkedIn and GitHub".'
    assert "Nothing is connected" in connect.next_step(False, None)


# ── help and dispatch ────────────────────────────────────────────────────────

def test_bare_artie_lists_connect_under_the_commands_you_run_yourself(capsys):
    assert entry.main([]) == 0
    out = capsys.readouterr().out
    section = out[out.index("Commands you run yourself"):]
    assert re.search(r"^  artie connect \[--private\]\s+sign in to GitHub and save your LinkedIn URL",
                     section, re.M)
    assert section.index("artie connect") < section.index("artie github login")


def test_artie_connect_dispatches_from_the_entry_point(monkeypatch):
    seen = []
    monkeypatch.setattr("harness.connect.main", lambda argv: seen.append(argv) or 0)
    assert entry.main(["connect", "--private"]) == 0 and seen == [["--private"]]


def test_connect_help_names_every_flag(capsys):
    with pytest.raises(SystemExit) as stop:
        connect.main(["--help"])
    assert stop.value.code == 0
    text = capsys.readouterr().out
    for flag in ("--private", "--reauth", "--github-only", "--linkedin-only", "--linkedin-url",
                 "--json", "--allow-writes"):
        assert flag in text


def test_connect_makes_no_network_client_of_its_own():
    for name in ("connect.py", "linkedin_url.py"):
        source = (ROOT / "harness" / name).read_text(encoding="utf-8")
        assert not re.search(r"^\s*(?:import|from)\s+(requests|httpx|urllib\.request|socket|"
                             r"http\.client|artie_gateway)\b", source, re.M), name


# â”€â”€ review fixes: stdout carries only the result; a bad environment token â”€â”€â”€â”€â”€

def test_the_prompt_goes_to_stderr_so_json_stdout_stays_one_document(kg, keyring, monkeypatch,  # noqa: F811
                                                                     capsys):
    import io

    fake = FakeGitHub()
    monkeypatch.setattr(gh, "_requests_transport", fake)
    monkeypatch.setattr(sys, "stdin", io.StringIO("linkedin.com/in/ada-lovelace\r\n"))
    args = connect._parser().parse_args(["--json"])
    code = connect.run(args, user_id=kg, interactive=True, transport=fake, sleep=_nosleep,
                       clock=lambda: 0, say=lambda line: None)
    seen = capsys.readouterr()
    assert code == 0
    doc = json.loads(seen.out)                       # the whole of stdout is the one document
    assert doc["linkedin"]["url"] == URL and seen.out.count("\n") == 1
    assert "Your LinkedIn profile URL" in seen.err and "Your LinkedIn profile URL" not in seen.out


def test_the_prompt_is_on_stderr_in_text_mode_too_and_closed_stdin_skips(kg, keyring,  # noqa: F811
                                                                         monkeypatch, capsys):
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    args = connect._parser().parse_args(["--linkedin-only"])
    assert connect.run(args, user_id=kg, interactive=True) == 0
    seen = capsys.readouterr()
    assert "Your LinkedIn profile URL" in seen.err and "Your LinkedIn profile URL" not in seen.out
    assert "LinkedIn skipped." in seen.out and _stored(kg) is None


def test_a_rejected_environment_token_is_not_answered_with_a_new_sign_in(keyring, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ART_GITHUB_TOKEN", TOKEN)
    run = Run(["--github-only"], fake=FakeGitHub(user_status=401), monkeypatch=monkeypatch)
    assert run.code == 1
    assert not any(c[1] == gh.DEVICE_CODE_URL for c in run.fake.calls)    # no device flow
    assert keyring.store == {}                                           # nothing stored
    assert "ART_GITHUB_TOKEN" in run.text and "Unset it" in run.text
    assert "Signed in" not in run.text and "  GitHub    not signed in" in run.text
    assert TOKEN not in run.text
