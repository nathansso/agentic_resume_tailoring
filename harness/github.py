"""The GitHub client (#353): device-flow login, the token store, and the repo list.

`artie connect` (#400; `artie github login` is the same sign-in alone) signs the user in with
GitHub's **device flow**, which needs
only a public client ID: no secret, no server, no redirect. The token it returns
is kept in one place, `resolve_token()`, which the repo list uses today and the
LinkedIn gateway client (#355) uses to identify the user to #354.

- **The token** lives in the OS keychain through the optional `keyring` package
  (service `art-mcp`, user `github-token`), else in `ART_GITHUB_TOKEN`, which
  also takes a personal access token. It is never stored in the database, never
  in an export bundle (`harness/export_import.py` does not import this module),
  never logged, and never put in an error, a `repr` or a tool result.
- **The client ID** is `ART_GITHUB_CLIENT_ID`, else `DEFAULT_CLIENT_ID` below.
  The package ships it once the owner registers the OAuth App (INSTALL.md,
  "GitHub OAuth App"); until then login fails naming that step.
- **Scopes.** `read:user` by default, which covers public repos. `--private` adds
  `repo`. A listing includes private repos only when the token holds `repo`.
- **Transport is injectable**, as the Jev client's is: a callable `(method, url,
  headers, form, timeout) -> (status, headers, text)`. Tests pass a fake, so
  nothing here touches the network in the suite.
- **This module, `harness/decisions/client.py`, `harness/toolchain.py` and
  `harness/linkedin_gateway.py` are the only network clients under `harness/`.**
  `tests/test_harness_boundary.py` allows exactly those.
  Nothing here is a generative call.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlencode

log = logging.getLogger(__name__)

# The OAuth App's public client ID. Empty until the owner registers the App with
# the device flow enabled (INSTALL.md, "GitHub OAuth App"); then it ships here.
DEFAULT_CLIENT_ID = "Ov23liPXidZbNgjP4Oh7"  # the ARTie OAuth App (device flow); public by design
CLIENT_ID_ENV, TOKEN_ENV = "ART_GITHUB_CLIENT_ID", "ART_GITHUB_TOKEN"
KEYRING_SERVICE, KEYRING_USER = "art-mcp", "github-token"

DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
API_URL = "https://api.github.com"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
API_VERSION = "2022-11-28"
USER_AGENT = "art-mcp"

DEFAULT_SCOPE, PRIVATE_SCOPE = "read:user", "repo"
README_MAX_CHARS = 3000          # "a few KB": enough to read what a repo is, not to ingest it
MAX_PER_PAGE = 50                # each repo costs two more requests (languages, README)

Transport = Callable[[str, str, Dict[str, str], Optional[Dict[str, str]], float],
                     Tuple[int, Dict[str, str], str]]


class GitHubError(Exception):
    """A failed call. `code` is a short machine-readable reason; the message never
    holds a token."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code, self.message = code, message


# ── configuration and the token store ────────────────────────────────────────

def client_id() -> str:
    return (os.environ.get(CLIENT_ID_ENV) or "").strip() or DEFAULT_CLIENT_ID


def _keyring():
    """The optional `keyring` module, or None (not installed)."""
    try:
        import keyring
        return keyring
    except Exception:  # noqa: BLE001 - not installed
        return None


def keychain_available() -> bool:
    """True when `keyring` is installed and has a usable (not the null/fail) backend."""
    kr = _keyring()
    if kr is None:
        return False
    try:
        backend = kr.get_keyring()
    except Exception:  # noqa: BLE001
        return False
    name = f"{type(backend).__module__}.{type(backend).__name__}".lower()
    return "fail" not in name and "null" not in name


def resolve_token() -> Optional[str]:
    """`ART_GITHUB_TOKEN`, else the keychain entry, else None. Never logged."""
    token, _ = _resolve()
    return token


def token_source() -> Optional[str]:
    """`env`, `keychain` or None: where `resolve_token()` found the token."""
    return _resolve()[1]


def _resolve() -> Tuple[Optional[str], Optional[str]]:
    token = (os.environ.get(TOKEN_ENV) or "").strip()
    if token:
        return token, "env"
    kr = _keyring()
    if kr is not None:
        try:
            token = (kr.get_password(KEYRING_SERVICE, KEYRING_USER) or "").strip()
        except Exception:  # noqa: BLE001 - no backend, locked: all mean "no token"
            token = ""
        if token:
            return token, "keychain"
    return None, None


def store_token(token: str) -> bool:
    """Save the token in the keychain. False when there is no keychain to hold it."""
    kr = _keyring()
    if kr is None or not token:
        return False
    try:
        kr.set_password(KEYRING_SERVICE, KEYRING_USER, token)
        return True
    except Exception:  # noqa: BLE001
        return False


def delete_token() -> bool:
    """Remove the keychain entry. True when one was removed."""
    kr = _keyring()
    if kr is None:
        return False
    try:
        if not kr.get_password(KEYRING_SERVICE, KEYRING_USER):
            return False
        kr.delete_password(KEYRING_SERVICE, KEYRING_USER)
        return True
    except Exception:  # noqa: BLE001
        return False


# ── transport ────────────────────────────────────────────────────────────────

def _requests_transport(method, url, headers, form, timeout):
    import requests

    try:
        resp = requests.request(method, url, headers=headers, data=form, timeout=timeout)
    except requests.Timeout as exc:
        raise GitHubError("timeout", "the request timed out") from exc
    except requests.RequestException as exc:
        raise GitHubError("network", type(exc).__name__) from exc
    return resp.status_code, {k.lower(): v for k, v in resp.headers.items()}, resp.text or ""


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def _message(body: Any, text: str) -> str:
    if isinstance(body, Mapping):
        return str(body.get("message") or body.get("error_description") or body.get("error") or "")[:200]
    return (text or "")[:200]


# ── the device flow ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DeviceCode:
    """GitHub's answer to the first step. `device_code` is a secret until the user
    authorises, so it stays out of `repr`."""
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int
    device_code: str = field(repr=False)


@dataclass(frozen=True)
class GrantedToken:
    token: str = field(repr=False)
    scopes: Tuple[str, ...] = ()


def scopes_for(private: bool) -> str:
    return f"{DEFAULT_SCOPE} {PRIVATE_SCOPE}" if private else DEFAULT_SCOPE


def require_client_id(cid: Optional[str]) -> str:
    cid = (cid or client_id()).strip()
    if not cid:
        raise GitHubError(
            "not_configured",
            "No GitHub client ID is set. The owner registers a GitHub OAuth App with the device "
            f"flow enabled (INSTALL.md, 'GitHub OAuth App') and its client ID ships in the "
            f"package; until then set {CLIENT_ID_ENV} to try it.")
    return cid


def request_device_code(scope: str = DEFAULT_SCOPE, *, client: Optional[str] = None,
                        transport: Optional[Transport] = None,
                        timeout: float = 20.0) -> DeviceCode:
    cid = require_client_id(client)
    status, _, text = (transport or _requests_transport)(
        "POST", DEVICE_CODE_URL, {"Accept": "application/json", "User-Agent": USER_AGENT},
        {"client_id": cid, "scope": scope}, timeout)
    body = _json(text)
    if not isinstance(body, Mapping):
        raise GitHubError("bad_response", f"GitHub answered {status} with no JSON")
    if body.get("error"):
        code = str(body["error"])
        if code == "device_flow_disabled":
            raise GitHubError(code, "The OAuth App does not have the device flow enabled: tick "
                                    "'Enable Device Flow' in its settings.")
        if code in ("incorrect_client_credentials", "unauthorized_client"):
            raise GitHubError("bad_client_id", "GitHub does not know this client ID.")
        raise GitHubError(code, _message(body, text))
    try:
        return DeviceCode(user_code=str(body["user_code"]),
                          verification_uri=str(body["verification_uri"]),
                          expires_in=int(body.get("expires_in") or 900),
                          interval=max(int(body.get("interval") or 5), 1),
                          device_code=str(body["device_code"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise GitHubError("bad_response", "the device-code response was incomplete") from exc


def poll_for_token(code: DeviceCode, *, client: Optional[str] = None,
                   transport: Optional[Transport] = None,
                   sleep: Callable[[float], None] = time.sleep,
                   clock: Callable[[], float] = time.monotonic,
                   timeout: float = 20.0) -> GrantedToken:
    """Wait for the user to authorise. `authorization_pending` keeps waiting;
    `slow_down` raises the interval (by the server's figure, else 5 s); an expired
    code or a refusal raises."""
    cid = require_client_id(client)
    send = transport or _requests_transport
    interval = code.interval
    deadline = clock() + code.expires_in
    while True:
        sleep(interval)
        if clock() > deadline:
            raise GitHubError("expired_token", "The code expired before it was entered. "
                                               "Run the login again.")
        status, _, text = send(
            "POST", ACCESS_TOKEN_URL, {"Accept": "application/json", "User-Agent": USER_AGENT},
            {"client_id": cid, "device_code": code.device_code, "grant_type": GRANT_TYPE}, timeout)
        body = _json(text)
        if not isinstance(body, Mapping):
            raise GitHubError("bad_response", f"GitHub answered {status} with no JSON")
        error = body.get("error")
        if not error:
            token = str(body.get("access_token") or "")
            if not token:
                raise GitHubError("bad_response", "GitHub returned no access token")
            scopes = tuple(s for s in re.split(r"[,\s]+", str(body.get("scope") or "")) if s)
            return GrantedToken(token=token, scopes=scopes)
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval = int(body.get("interval") or interval + 5)
            continue
        if error == "expired_token":
            raise GitHubError("expired_token", "The code expired before it was entered. "
                                               "Run the login again.")
        if error == "access_denied":
            raise GitHubError("access_denied", "The authorisation was declined on GitHub.")
        raise GitHubError(str(error), _message(body, text))


# ── the API client ───────────────────────────────────────────────────────────

class GitHubClient:
    """Read-only calls with a token. Reusable: #355 builds on `resolve_token()` and
    this class's `user()` to identify the user to the gateway."""

    def __init__(self, token: str, *, transport: Optional[Transport] = None,
                 api: str = API_URL, timeout: float = 20.0):
        if not token:
            raise ValueError("GitHubClient needs a token")
        self._token = token
        self._transport = transport or _requests_transport
        self.api, self.timeout = api.rstrip("/"), timeout

    def __repr__(self) -> str:
        return f"GitHubClient(api={self.api!r})"

    def _get(self, path: str, params: Optional[Mapping[str, Any]] = None,
             accept: str = "application/vnd.github+json") -> Tuple[int, Dict[str, str], str]:
        url = f"{self.api}{path}" + (f"?{urlencode(params)}" if params else "")
        headers = {"Authorization": f"Bearer {self._token}", "Accept": accept,
                   "X-GitHub-Api-Version": API_VERSION, "User-Agent": USER_AGENT}
        status, resp_headers, text = self._transport("GET", url, headers, None, self.timeout)
        if status == 401:
            raise GitHubError("bad_token", "GitHub rejected the token. The user signs in again with: "
                              + login_hint())
        if status in (403, 429) and resp_headers.get("x-ratelimit-remaining") == "0":
            raise GitHubError("rate_limited", "GitHub's rate limit is used up; try again later.")
        return status, resp_headers, text

    def user(self) -> Dict[str, Any]:
        """`{login, id, name, scopes}`. `scopes` come from the `X-OAuth-Scopes` header;
        a token that does not report them (a fine-grained one) has none, which is
        treated as public-only."""
        status, headers, text = self._get("/user")
        body = _json(text)
        if status != 200 or not isinstance(body, Mapping) or not body.get("login"):
            raise GitHubError(str(status), _message(body, text) or "could not read the user")
        scopes = [s for s in re.split(r"[,\s]+", headers.get("x-oauth-scopes", "")) if s]
        return {"login": str(body["login"]), "id": body.get("id"), "name": body.get("name"),
                "scopes": scopes}

    def repos(self, *, page: int = 1, per_page: int = 20,
              include_private: bool = False) -> Tuple[List[Dict[str, Any]], Optional[int]]:
        """One page of the user's own repos, most recently pushed first, and the next
        page number (None on the last). Private repos are asked for only when
        `include_private`; the caller decides that from the token's scopes."""
        status, headers, text = self._get("/user/repos", {
            "affiliation": "owner", "sort": "pushed", "direction": "desc",
            "visibility": "all" if include_private else "public",
            "per_page": per_page, "page": page})
        body = _json(text)
        if status != 200 or not isinstance(body, list):
            raise GitHubError(str(status), _message(body, text) or "could not list repos")
        has_next = 'rel="next"' in headers.get("link", "")
        return [r for r in body if isinstance(r, Mapping)], (page + 1 if has_next else None)

    def languages(self, full_name: str) -> List[str]:
        """Language names, biggest first. Empty when GitHub has none or the call fails."""
        try:
            status, _, text = self._get(f"/repos/{full_name}/languages")
        except GitHubError:
            return []
        body = _json(text)
        if status != 200 or not isinstance(body, Mapping):
            return []
        return [k for k, _ in sorted(body.items(), key=lambda kv: -(kv[1] or 0))]

    def readme(self, full_name: str, max_chars: int = README_MAX_CHARS) -> Tuple[Optional[str], bool]:
        """`(excerpt, truncated)`; `(None, False)` for a repo with no README."""
        try:
            status, _, text = self._get(f"/repos/{full_name}/readme",
                                        accept="application/vnd.github.raw+json")
        except GitHubError:
            return None, False
        if status != 200 or not text.strip():
            return None, False
        text = text.replace("\r\n", "\n").strip()
        return (text[:max_chars].rstrip(), True) if len(text) > max_chars else (text, False)


# ── the tool ─────────────────────────────────────────────────────────────────

def login_command(private: bool = False) -> str:
    """The command a user runs to sign in: `artie connect` (#400), which signs in to GitHub
    and asks for the LinkedIn URL; `artie github login` is the same sign-in alone.
    `/artie:setup` puts `artie` on PATH with `uv tool install` (#385), so this is the short
    form; `login_fallback` is the rest."""
    return "artie connect" + (" --private" if private else "")


def login_fallback(private: bool = False) -> str:
    """The same command through the pinned wheel, for a machine where `artie` is not on PATH
    (a plugin install that never ran the setup's install step) (#369)."""
    from harness.hooks import UVX  # the one pin; imported here to keep this module light
    return f"{UVX} {login_command(private)}"


def login_hint(private: bool = False) -> str:
    """The short command, then the long one for when `artie` is not on PATH."""
    return f"{login_command(private)} (if `artie` is not on PATH: {login_fallback(private)})"


def _error(code: str, message: str) -> Dict[str, Any]:
    return {"error": {"code": code, "message": message, "suggestions": []}}


def _repo_row(client: GitHubClient, repo: Mapping[str, Any], include_readme: bool) -> Dict[str, Any]:
    full = str(repo.get("full_name") or "")
    excerpt, truncated = (client.readme(full) if include_readme and full else (None, False))
    clone_url = repo.get("clone_url") or None
    return {
        "name": repo.get("name"),
        "full_name": full,
        "description": repo.get("description"),
        "languages": client.languages(full) if full else [],
        "topics": list(repo.get("topics") or []),
        "stargazers": int(repo.get("stargazers_count") or 0),
        "pushed_at": repo.get("pushed_at"),
        "fork": bool(repo.get("fork")),
        "archived": bool(repo.get("archived")),
        "private": bool(repo.get("private")),
        "html_url": repo.get("html_url"),
        "clone_url": clone_url,
        "clone_command": f"git clone {clone_url}" if clone_url else None,
        "evidence_source": f"github:{repo.get('name')}",
        "readme_excerpt": excerpt,
        "readme_truncated": truncated,
    }


def list_github_repos(page: int = 1, per_page: int = 20, include_private: bool = True,
                      include_readme: bool = True, *,
                      client: Optional[GitHubClient] = None) -> Dict[str, Any]:
    """The `list_github_repos` tool body. Errors come back in the contract's `error`
    shape, not as exceptions; a token is never part of the result."""
    if client is None:
        token = resolve_token()
        if not token:
            return _error("not_logged_in", "Not signed in to GitHub. Have the user run this in "
                                           f"their own terminal: {login_command()} (or "
                                           f"{login_command(private=True)} to include private "
                                           "repos), then call this again. If `artie` is not on "
                                           f"their PATH, the same command is: {login_fallback()}")
        client = GitHubClient(token)
    per_page = max(1, min(int(per_page), MAX_PER_PAGE))
    try:
        who = client.user()
        private_ok = "repo" in who["scopes"]
        private = bool(include_private and private_ok)
        repos, nxt = client.repos(page=page, per_page=per_page, include_private=private)
        rows = [_repo_row(client, r, include_readme) for r in repos
                if private or not r.get("private")]
    except GitHubError as exc:
        return _error(exc.code, exc.message)
    note = None
    if include_private and not private_ok:
        note = ("Public repos only: the token lacks the `repo` scope. To include private ones "
                f"the user runs: {login_hint(private=True)}")
    return {"login": who["login"], "scopes": who["scopes"], "private_included": private,
            "page": page, "per_page": per_page, "next_page": nxt, "repos": rows, "note": note}


def login_status() -> Dict[str, Any]:
    """Where the token is and whom it signs in. Never the token."""
    token, source = _resolve()
    out: Dict[str, Any] = {"logged_in": False, "source": source, "login": None, "scopes": [],
                           "private_scope": False, "client_id_set": bool(client_id()),
                           "keychain": keychain_available()}
    if not token:
        return out
    try:
        who = GitHubClient(token).user()
    except GitHubError as exc:
        out["error"] = {"code": exc.code, "message": exc.message}
        return out
    out.update(logged_in=True, login=who["login"], scopes=who["scopes"],
               private_scope="repo" in who["scopes"])
    return out


__all__: Sequence[str] = [
    "GitHubClient", "GitHubError", "DeviceCode", "GrantedToken", "request_device_code",
    "poll_for_token", "resolve_token", "token_source", "store_token", "delete_token",
    "keychain_available", "client_id", "require_client_id", "list_github_repos", "login_status", "scopes_for"]
