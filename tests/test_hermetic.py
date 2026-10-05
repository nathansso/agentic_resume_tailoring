"""The suite is hermetic (issue #249): no `.env` secrets, no outbound network.

During #210 a full local run POSTed a signup to the hosted Supabase project.
`eval/tailoring_benchmark.py` popped the Supabase variables and `config.py`'s
`load_dotenv()` put them back. These tests pin the three defences:

* `tests/_hermetic.py::isolate_environment` removes secrets and turns `.env`
  loading off before any app module is imported;
* the network guard refuses every connection but loopback and the test Postgres;
* the benchmark blanks (rather than pops) the Supabase variables, so a later
  `load_dotenv()` cannot restore them.

No test here uses the network: the guard trips before a packet is sent, and the
exemption is proved with a fake `connect`.
"""
import os
import shutil
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import _hermetic
from _hermetic import NetworkBlockedError

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
PUBLIC_IP = "203.0.113.9"           # TEST-NET-3, never routable

FAKE_ENV_FILE = """\
DATABASE_URL=postgresql://prod_user:prod_pw@db.fake-project.supabase.co:5432/postgres
SUPABASE_URL=https://fake-project.supabase.co
SUPABASE_ANON_KEY=eyJfake.anon.key
SUPABASE_JWT_SECRET=fake-jwt-secret
OPENAI_API_KEY=sk-fake-openai
ANTHROPIC_API_KEY=sk-ant-fake
TYPESAFE_API_KEY=ts-fake
GITHUB_TOKEN=ghp_fake
LANGCHAIN_API_KEY=ls-fake
"""
LEAKED = ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_JWT_SECRET",
          "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TYPESAFE_API_KEY", "GITHUB_TOKEN",
          "LANGCHAIN_API_KEY")


def _run(args, cwd, env, timeout=300):
    return subprocess.run([sys.executable, *args], cwd=cwd, env=env, text=True,
                          capture_output=True, timeout=timeout)


def _real_user_env():
    """What a developer's shell looks like: dotenv on, no test scrub."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHON_DOTENV_DISABLED"}
    env["PYTHONPATH"] = os.pathsep.join([str(TESTS), str(ROOT)])
    return env


# ── the network guard ────────────────────────────────────────────────────────

def test_a_connection_to_a_public_host_is_blocked_and_names_the_test_and_target():
    with _hermetic.expecting_block():
        with pytest.raises(NetworkBlockedError) as exc:
            socket.socket().connect((PUBLIC_IP, 443))
    msg = str(exc.value)
    assert PUBLIC_IP in msg and "443" in msg
    assert "test_a_connection_to_a_public_host_is_blocked" in msg      # names the test
    assert "integration" in msg                                          # says how to opt in


def test_connect_ex_and_create_connection_are_blocked_too():
    with _hermetic.expecting_block():
        with pytest.raises(NetworkBlockedError):
            socket.socket().connect_ex((PUBLIC_IP, 80))
        with pytest.raises(NetworkBlockedError):
            socket.create_connection((PUBLIC_IP, 80), timeout=1)


def test_a_dns_lookup_for_a_public_name_is_blocked_before_any_query():
    with _hermetic.expecting_block():
        with pytest.raises(NetworkBlockedError, match="DNS lookup"):
            socket.getaddrinfo("api.example.com", 443)
        with pytest.raises(NetworkBlockedError):
            socket.create_connection(("project.supabase.co", 443), timeout=1)


def test_a_postgres_driver_connecting_in_c_is_blocked_by_the_engine_hook():
    """libpq opens its own socket, so `socket` never sees psycopg2; the engine does."""
    pytest.importorskip("psycopg2")
    from sqlalchemy import create_engine

    engine = create_engine("postgresql://u:p@db.fake-project.supabase.co:5432/postgres")
    with _hermetic.expecting_block():
        with pytest.raises(NetworkBlockedError, match="db.fake-project.supabase.co"):
            engine.connect()


def test_loopback_is_allowed():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        client = socket.create_connection(("127.0.0.1", server.getsockname()[1]), timeout=5)
        client.close()
    finally:
        server.close()
    assert socket.getaddrinfo("localhost", 80)
    for host in ("127.0.0.1", "127.9.9.9", "::1", "[::1]", "localhost", "0.0.0.0", "", None):
        assert _hermetic.host_allowed(host, 5432), host


def test_public_hosts_are_not_allowed():
    for host in (PUBLIC_IP, "8.8.8.8", "db.fake-project.supabase.co", "api.github.com",
                 "10.0.0.5", "localhost.evil.example"):
        assert not _hermetic.host_allowed(host, 443), host


def test_the_test_postgres_host_and_port_are_allowed_and_nothing_else_on_it(monkeypatch):
    monkeypatch.setenv("ART_TEST_DATABASE_URL", "postgresql://art:art@10.1.2.3:5433/art")
    monkeypatch.setattr(_hermetic._STATE, "allowed_pg", None)           # re-read the env
    assert _hermetic.host_allowed("10.1.2.3", 5433)
    assert not _hermetic.host_allowed("10.1.2.3", 22)                    # right host, wrong port
    assert not _hermetic.host_allowed("10.1.2.4", 5433)                  # wrong host


def test_a_named_test_postgres_host_is_allowed(monkeypatch):
    monkeypatch.setenv("ART_TEST_DATABASE_URL", "postgresql://art:art@pg.internal/art")
    monkeypatch.setattr(_hermetic._STATE, "allowed_pg", None)
    assert _hermetic.host_allowed("pg.internal", 5432)
    assert not _hermetic.host_allowed("pg.internal", 6543)
    assert not _hermetic.host_allowed("other.internal", 5432)


def test_a_disarmed_guard_passes_connects_through(monkeypatch):
    """What an integration test sees: the real connect, here replaced so no packet is sent."""
    seen = []
    monkeypatch.setitem(_hermetic._REAL, "connect", lambda self, address: seen.append(address))
    monkeypatch.setattr(_hermetic._STATE, "armed", False)
    socket.socket().connect((PUBLIC_IP, 443))
    assert seen == [(PUBLIC_IP, 443)]


def test_a_non_integration_test_runs_with_the_guard_armed():
    assert _hermetic._STATE.armed is True


# ── pytest wiring: integration exemption, opt-in secrets, swallowed attempts ──

_PROBE = '''
import os, socket
import pytest
import _hermetic


def test_unmarked_is_guarded():
    assert _hermetic._STATE.armed
    with _hermetic.expecting_block():
        with pytest.raises(_hermetic.NetworkBlockedError):
            socket.socket().connect(("203.0.113.9", 443))


@pytest.mark.integration
def test_marked_is_exempt(monkeypatch, live_secrets):
    assert not _hermetic._STATE.armed
    seen = []
    monkeypatch.setitem(_hermetic._REAL, "connect", lambda s, a: seen.append(a))
    socket.socket().connect(("203.0.113.9", 443))        # passes through; nothing is sent
    assert seen == [("203.0.113.9", 443)]


@pytest.mark.integration
def test_marked_gets_the_opted_in_key(live_secrets):
    got = live_secrets("OPENAI_API_KEY")
    assert got["OPENAI_API_KEY"] == "sk-fake-opt-in"      # the shell's value, captured before the scrub
    assert os.environ["OPENAI_API_KEY"] == "sk-fake-opt-in"


def test_unmarked_sees_no_secrets_and_cannot_ask_for_them(request):
    for name in ("OPENAI_API_KEY", "DATABASE_URL", "SUPABASE_URL", "TYPESAFE_API_KEY"):
        assert name not in os.environ, name
    assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"
    assert os.environ["ART_TEST_DATABASE_URL"] == "postgresql://art:art@10.1.2.3:5433/art"
    with pytest.raises(pytest.fail.Exception):
        request.getfixturevalue("live_secrets")


def test_database_and_supabase_are_never_opt_in():
    with pytest.raises(KeyError):
        _hermetic.opt_in_secret("DATABASE_URL")
    with pytest.raises(KeyError):
        _hermetic.opt_in_secret("SUPABASE_URL")


def test_a_swallowed_attempt_still_fails_the_test():
    try:
        socket.socket().connect(("203.0.113.9", 443))
    except Exception:
        pass
'''


def test_pytest_applies_the_guard_exemption_secrets_and_swallow_check(tmp_path):
    probe = tmp_path / "test_probe.py"
    probe.write_text(_PROBE, encoding="utf-8")
    env = _real_user_env()
    env["OPENAI_API_KEY"] = "sk-fake-opt-in"
    env["DATABASE_URL"] = "postgresql://prod:prod@db.fake-project.supabase.co/postgres"
    env["SUPABASE_URL"] = "https://fake-project.supabase.co"
    env["TYPESAFE_API_KEY"] = "ts-fake"
    env["ART_TEST_DATABASE_URL"] = "postgresql://art:art@10.1.2.3:5433/art"
    # Load the suite's own conftest as a plugin so the probe gets its fixtures and hooks.
    out = _run(["-m", "pytest", "-p", "conftest", "-p", "no:cacheprovider", "-rA", "-q",
                "-o", "markers=integration: opt in", "--rootdir", str(tmp_path), str(probe)], cwd=tmp_path, env=env)
    report = out.stdout + out.stderr
    for name in ("test_unmarked_is_guarded", "test_marked_is_exempt",
                 "test_marked_gets_the_opted_in_key",
                 "test_unmarked_sees_no_secrets_and_cannot_ask_for_them",
                 "test_database_and_supabase_are_never_opt_in"):
        assert f"PASSED test_probe.py::{name}" in report, report[-3000:]
    # The attempt was swallowed by the test's own except, and still failed it.
    assert "ERROR test_probe.py::test_a_swallowed_attempt_still_fails_the_test" in report, report[-3000:]
    assert "203.0.113.9:443" in report
    assert "hermetic:" in report                                  # the summary line printed


# ── `.env` cannot reach the process ──────────────────────────────────────────

def _project_with_fake_dotenv(tmp_path, depth=0):
    """The real `config.py` plus a `.env` full of real-looking values.

    `config.py` finds `.env` by walking up from its own directory, so a copy is the
    realistic stand-in for the developer's file. With `depth` > 0 the `.env` sits in
    a PARENT of the directory holding `config.py` and of the cwd, which is how a
    checkout nested under another one (an agent worktree under `.claude/worktrees/`)
    finds the main checkout's `.env`: the case that reached Supabase's signup
    endpoint from the benchmark tests."""
    project = tmp_path.joinpath(*[f"nest{i}" for i in range(depth)])
    project.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "config.py", project / "config.py")
    (tmp_path / ".env").write_text(FAKE_ENV_FILE, encoding="utf-8")
    return project


def _clean_env():
    env = _real_user_env()
    for name in list(env):
        if _hermetic.is_secret_name(name):
            del env[name]
    env.pop("ART_DATA_DIR", None)
    # On the Postgres leg this is set, and the benchmark's `make_throwaway_db` then
    # CREATEs a real database on that server (and the URL it returns is Postgres, not
    # SQLite). These subprocess scripts are about `.env`, not storage: keep them off it.
    env.pop("ART_TEST_DATABASE_URL", None)
    return env


@pytest.mark.parametrize("depth", [0, 3], ids=["dotenv-beside-config", "dotenv-in-a-parent-directory"])
def test_a_dotenv_with_real_looking_values_does_not_reach_the_process(tmp_path, depth):
    project = _project_with_fake_dotenv(tmp_path, depth)
    script = textwrap.dedent(f"""
        import os, sys
        import _hermetic
        _hermetic.isolate_environment()
        _hermetic.install_network_guard()
        import config
        import database.db as db
        import eval.tailoring_benchmark
        assert config.__file__.startswith({str(project)!r}), config.__file__
        leaked = [k for k in {LEAKED!r} if os.environ.get(k)]
        assert not leaked, leaked
        assert config.DATABASE_URL.startswith("sqlite:///"), config.DATABASE_URL
        assert db.engine.url.get_backend_name() == "sqlite", db.engine.url
        assert "fake-project" not in str(db.engine.url)
        assert config.OPENAI_API_KEY is None and config.GITHUB_TOKEN is None
        print("hermetic-ok")
    """)
    out = _run(["-c", script], cwd=project, env={**_clean_env(),
               "PYTHONPATH": os.pathsep.join([str(project), str(TESTS), str(ROOT)])})
    assert out.returncode == 0 and "hermetic-ok" in out.stdout, out.stdout + out.stderr


@pytest.mark.parametrize("depth", [0, 3], ids=["dotenv-beside-config", "dotenv-in-a-parent-directory"])
def test_the_fake_dotenv_would_leak_without_the_isolation(tmp_path, depth):
    """Control: the same project, no isolation. Proves the test above is not vacuous."""
    project = _project_with_fake_dotenv(tmp_path, depth)
    script = ("import os, config; "
              "assert config.__file__.startswith(%r); "
              "assert os.environ['DATABASE_URL'].startswith('postgresql://prod_user'); "
              "assert os.environ['SUPABASE_URL']; print('leaked')" % str(project))
    out = _run(["-c", script], cwd=project, env={**_clean_env(),
               "PYTHONPATH": os.pathsep.join([str(project), str(ROOT)])})
    assert out.returncode == 0 and "leaked" in out.stdout, out.stdout + out.stderr


def test_the_session_itself_started_without_secrets_and_with_dotenv_off():
    assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"
    leaked = [k for k in os.environ if _hermetic.is_secret_name(k)]
    assert not leaked, leaked
    import config
    import database.db as db

    assert "supabase" not in str(db.engine.url)
    assert db.engine.url.get_backend_name() in ("sqlite", "postgresql")
    assert Path(config.APP_DATA_DIR) != Path.home() / ".art"            # not the real data dir


def test_the_secret_name_rules():
    for name in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE", "TYPESAFE_API_KEY",
                 "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN", "LANGSMITH_API_KEY",
                 "LANGCHAIN_TRACING_V2", "SOMETHING_NEW_KEY", "X_SECRET", "DB_PASSWORD",
                 "HF_TOKEN", "ART_MCP_DATABASE_URL"):
        assert _hermetic.is_secret_name(name), name
    for name in ("ART_TEST_DATABASE_URL", "PATH", "ART_JEV_MODE", "ART_DATA_DIR",
                 "PYTHON_DOTENV_DISABLED", "GITHUB_USERNAME", "LLM_PROVIDER"):
        assert not _hermetic.is_secret_name(name), name


# ── the benchmark cannot regain what it removes ──────────────────────────────

_FIND_FAKE_DOTENV = """
import dotenv, dotenv.main
# The benchmark imports the checkout's own config, which would find the checkout's
# `.env`. Point dotenv's discovery at the fake file instead, as if it were that one.
_fake = {fake!r}
dotenv.find_dotenv = dotenv.main.find_dotenv = lambda *a, **k: _fake
"""


def test_the_benchmark_keeps_supabase_blank_even_after_config_loads_dotenv(tmp_path):
    """The #210 incident, as a user's shell would see it: `.env` present, dotenv on."""
    (tmp_path / ".env").write_text(FAKE_ENV_FILE, encoding="utf-8")
    script = _FIND_FAKE_DOTENV.format(fake=str(tmp_path / ".env")) + textwrap.dedent(f"""
        import os
        from pathlib import Path
        from eval.tailoring_benchmark import _prepare_environment
        _prepare_environment(Path({str(tmp_path / "work")!r}))
        import config                       # load_dotenv() runs here, after the pops
        assert os.environ["OPENAI_API_KEY"] == "sk-fake-openai"     # dotenv did load
        for name in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_JWT_SECRET",
                     "SUPABASE_EXTRA_FROM_SHELL"):
            assert os.environ[name] == "", name + " was restored to a non-empty value by load_dotenv"
        assert not os.getenv("SUPABASE_URL"), "SUPABASE_URL reads as set"
        from database.auth import supabase_configured
        assert supabase_configured() is False, "Supabase auth would be used"
        db_url = os.environ["DATABASE_URL"]
        assert db_url.startswith("sqlite:///"), "benchmark did not use its own SQLite store: " + db_url.split("@")[-1]
        assert "fake-project" not in db_url, "DATABASE_URL came from .env"
        print("benchmark-ok")
    """)
    (tmp_path / "work").mkdir()
    env = {**_clean_env(), "SUPABASE_EXTRA_FROM_SHELL": "x"}
    out = _run(["-c", script], cwd=tmp_path, env=env)
    assert out.returncode == 0 and "benchmark-ok" in out.stdout, out.stdout + out.stderr


def test_popping_the_variables_is_what_let_dotenv_restore_them(tmp_path):
    """Control for the test above: the old behaviour (pop) loses to `load_dotenv()`."""
    project = _project_with_fake_dotenv(tmp_path)
    script = ("import os; os.environ['SUPABASE_URL']='x'; os.environ.pop('SUPABASE_URL'); "
              "import config; assert os.environ['SUPABASE_URL'] == 'https://fake-project.supabase.co'; "
              "print('restored')")
    out = _run(["-c", script], cwd=project, env={**_clean_env(),
               "PYTHONPATH": os.pathsep.join([str(project), str(ROOT)])})
    assert out.returncode == 0 and "restored" in out.stdout, out.stdout + out.stderr
