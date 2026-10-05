"""Hermetic test environment (issue #249): no secrets, no outbound network.

Imported first by `tests/conftest.py`, before anything from the app, and stdlib
only. Two jobs:

1. `isolate_environment()` removes every credential and connection string from
   `os.environ` and turns `.env` loading off, so an import-time `load_dotenv()`
   (`config.py`) or a module-level engine (`database/db.py`) cannot see the
   developer's secrets or bind to a production database.
2. `install_network_guard()` makes `connect` / `getaddrinfo` (and the SQLAlchemy
   `do_connect` hook, which is how libpq-backed drivers bypass `socket`) refuse any
   host but loopback and the `ART_TEST_DATABASE_URL` server. Tests marked
   `@pytest.mark.integration` are exempt; that mark is the opt-in.

Why this exists: during #210 a local run POSTed a signup to the hosted Supabase
project. `eval/tailoring_benchmark.py` popped the Supabase variables, and
`config.py`'s `load_dotenv()` put them back.
"""
from __future__ import annotations

import atexit
import ipaddress
import os
import shutil
import socket
import tempfile
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import urlsplit

# ── environment ──────────────────────────────────────────────────────────────

# Named variables that are removed from the test environment.
SECRET_ENV_NAMES = (
    "DATABASE_URL",
    "ART_MCP_DATABASE_URL",
    "ART_IMPORT_SOURCE_URL",
    "TYPESAFE_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GITHUB_TOKEN",
    "GITHUB_CLIENT_SECRET",
    "BRIGHTDATA_API_KEY",
)
# Anything under these prefixes goes too (Supabase, LangSmith / LangChain tracing).
SECRET_ENV_PREFIXES = ("SUPABASE_", "LANGSMITH_", "LANGCHAIN_")
# ...and anything whose name ends like a credential, so a new key added to `.env`
# tomorrow is covered without editing this file.
SECRET_ENV_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "DATABASE_URL")
# Matches above that must survive. `ART_TEST_DATABASE_URL` is the Postgres leg.
SECRET_ENV_ALLOWLIST = frozenset({"ART_TEST_DATABASE_URL"})

# Set to "1" so ML libraries read their local cache instead of checking the network.
OFFLINE_ENV = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE")

# Values an `@pytest.mark.integration` test may ask for through the `live_secrets`
# fixture. Captured before the scrub (from the process environment, then `.env`)
# and held in this module only; `DATABASE_URL` and `SUPABASE_*` are deliberately
# not here, so no test, integration or not, can be handed production storage.
OPT_IN_NAMES = (
    "TYPESAFE_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GITHUB_TOKEN",
    "GITHUB_USERNAME",
    "LLM_PROVIDER",
)

_OPT_IN: dict[str, str] = {}
_SCRATCH: dict[str, str] = {}
_DOTENV_OFF = "PYTHON_DOTENV_DISABLED"


def is_secret_name(name: str) -> bool:
    """True when `name` is a credential-bearing variable the suite removes."""
    n = name.upper()
    if n in SECRET_ENV_ALLOWLIST:
        return False
    return (n in SECRET_ENV_NAMES
            or n.startswith(SECRET_ENV_PREFIXES)
            or n.endswith(SECRET_ENV_SUFFIXES))


def _capture_opt_in(environ) -> None:
    for name in OPT_IN_NAMES:
        val = (environ.get(name) or "").strip()
        if val:
            _OPT_IN.setdefault(name, val)
    missing = [n for n in OPT_IN_NAMES if n not in _OPT_IN]
    if not missing:
        return
    try:  # the developer's key may live only in `.env`; read it, never export it
        from dotenv import dotenv_values, find_dotenv
        values = dotenv_values(find_dotenv())
    except Exception:
        return
    for name in missing:
        val = (values.get(name) or "").strip()
        if val:
            _OPT_IN[name] = val


def opt_in_secret(name: str) -> str:
    """The captured value of an opt-in variable, or '' when the developer has none."""
    if name not in OPT_IN_NAMES:
        raise KeyError(f"{name} is not an opt-in secret; see OPT_IN_NAMES in tests/_hermetic.py")
    return _OPT_IN.get(name, "")


def isolate_environment(environ=None) -> list[str]:
    """Scrub secrets from `environ`, disable `.env` loading, point data at a scratch dir.

    Returns the names removed (never the values). Safe to call twice.
    """
    environ = os.environ if environ is None else environ
    _capture_opt_in(environ)
    removed = sorted(k for k in list(environ) if is_secret_name(k))
    for k in removed:
        del environ[k]
    environ[_DOTENV_OFF] = "1"
    # Hugging Face's hub client checks for model updates over the network on every
    # load, even from a warm cache (found by the guard in #249: the real embedding
    # model loads in tests that never mock it). Offline reads the cache and sends
    # nothing; a missing model fails the same way it does without the package.
    for var in OFFLINE_ENV:
        environ[var] = "1"
    # `database.db` builds a module-level engine from config at import; with no
    # DATABASE_URL that is SQLite under ART_DATA_DIR. Keep it off the real ~/.art.
    if "dir" not in _SCRATCH:
        _SCRATCH["dir"] = tempfile.mkdtemp(prefix="art_tests_data_")
        atexit.register(shutil.rmtree, _SCRATCH["dir"], True)
    environ["ART_DATA_DIR"] = _SCRATCH["dir"]
    return removed


# ── network guard ────────────────────────────────────────────────────────────

class NetworkBlockedError(ConnectionError):
    """An outbound connection a hermetic test is not allowed to make."""


_STATE = SimpleNamespace(armed=True, installed=False, blocked=[], allowed_pg=None)
_REAL = {}


def _test_pg() -> tuple[set[str], int | None]:
    """Host names / addresses and port of ART_TEST_DATABASE_URL (the Postgres leg)."""
    if _STATE.allowed_pg is not None:
        return _STATE.allowed_pg
    hosts: set[str] = set()
    port = None
    url = os.environ.get("ART_TEST_DATABASE_URL")
    if url:
        parts = urlsplit(url)
        if parts.hostname:
            hosts.add(parts.hostname.lower())
            port = parts.port or 5432
            getaddr = _REAL.get("getaddrinfo") or socket.getaddrinfo
            try:  # drivers resolve before they connect, so connect() sees addresses
                for info in getaddr(parts.hostname, port):
                    hosts.add(info[4][0].lower())
            except OSError:
                pass
    _STATE.allowed_pg = (hosts, port)
    return _STATE.allowed_pg


def _loopback(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


def host_allowed(host, port=None) -> bool:
    """Loopback, or the test Postgres server (host and, when given, port)."""
    if host is None or host == "" or host == b"":
        return True                      # a unix-socket / default-local connection
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = str(host).strip("[]").lower()
    if host.startswith("/"):
        return True                      # unix socket directory (libpq)
    if _loopback(host):
        return True
    pg_hosts, pg_port = _test_pg()
    if host in pg_hosts:
        return port is None or pg_port is None or int(port) == pg_port
    return False


def _record(host, port, kind: str) -> NetworkBlockedError:
    test = os.environ.get("PYTEST_CURRENT_TEST", "<outside a test: collection or session setup>")
    _STATE.blocked.append({"test": test, "host": host, "port": port, "kind": kind})
    return NetworkBlockedError(
        f"hermetic test suite: blocked outbound {kind} to {host}:{port} from {test}. "
        "Only localhost and the ART_TEST_DATABASE_URL server are reachable. "
        "Mock the call, or mark the test @pytest.mark.integration if it must use the network."
    )


def _split(address):
    """(host, port) from a socket address; None host for unix sockets."""
    if isinstance(address, (str, bytes)):
        return None, None
    try:
        return address[0], address[1]
    except Exception:
        return None, None


def _guarded_connect(self, address):
    if _STATE.armed:
        host, port = _split(address)
        if not host_allowed(host, port):
            raise _record(host, port, "connect")
    return _REAL["connect"](self, address)


def _guarded_connect_ex(self, address):
    if _STATE.armed:
        host, port = _split(address)
        if not host_allowed(host, port):
            raise _record(host, port, "connect")
    return _REAL["connect_ex"](self, address)


def _guarded_getaddrinfo(host, port, *args, **kwargs):
    if _STATE.armed and not host_allowed(host):
        raise _record(host, port, "DNS lookup")
    return _REAL["getaddrinfo"](host, port, *args, **kwargs)


def _guard_sqlalchemy() -> None:
    """libpq (psycopg2) opens its own socket in C, so `socket` never sees it."""
    try:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine
    except Exception:
        return

    @event.listens_for(Engine, "do_connect")
    def _check_db_host(dialect, conn_rec, cargs, cparams):
        if not _STATE.armed or dialect.name == "sqlite":
            return None
        raw = cparams.get("host") or cparams.get("hostaddr") or ""
        port = cparams.get("port")
        for host in str(raw).split(","):
            if not host_allowed(host.strip(), port):
                raise _record(host.strip(), port, "database connect")
        return None


def install_network_guard() -> None:
    """Patch the socket layer once per process. Armed until a test disarms it."""
    if _STATE.installed:
        return
    _REAL["connect"] = socket.socket.connect
    _REAL["connect_ex"] = socket.socket.connect_ex
    _REAL["getaddrinfo"] = socket.getaddrinfo
    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
    socket.getaddrinfo = _guarded_getaddrinfo
    _guard_sqlalchemy()
    _STATE.installed = True


def set_armed(armed: bool) -> None:
    _STATE.armed = armed


def blocked_attempts() -> list[dict]:
    return list(_STATE.blocked)


def forget_blocked(since: int = 0) -> None:
    """Drop records after index `since`. For tests that trip the guard on purpose."""
    del _STATE.blocked[since:]


@contextmanager
def expecting_block():
    """Arm the guard and discard the records made inside, for tests of the guard itself."""
    was, start = _STATE.armed, len(_STATE.blocked)
    _STATE.armed = True
    try:
        yield
    finally:
        _STATE.armed = was
        forget_blocked(start)
