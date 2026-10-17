"""The toolchain downloader (#384): a pinned Tectonic, fetched on first need.

Rendering a PDF and measuring bullet lines need a LaTeX engine. When the user has
none, `ensure_tectonic()` provisions **Tectonic 0.16.9** (the engine the line-budget
calibration in `agents/checks.py` was fitted on) at `$ART_DATA_DIR/bin/tectonic[.exe]`,
so a plugin user never installs LaTeX:

- **Pinned.** `ASSETS` holds each platform's release asset name and SHA-256 from the
  official `tectonic@0.16.9` GitHub release. Only those URLs are fetched; a redirect is
  followed only to GitHub's own release-asset hosts over HTTPS.
- **Verified.** The archive's SHA-256 must match the pin. A mismatch is refused and the
  download deleted, so no executable is left behind. Only the one `tectonic[.exe]`
  member is extracted (never a path from the archive), then marked executable.
- **Once at a time.** A lock file in the bin directory keeps two processes from
  downloading together; the second waits for the first and uses its engine.
- **Never raises.** Every failure comes back as `Provision(path=None, reason=...)`,
  and the caller returns today's "no LaTeX engine" result with that reason. A failure
  is remembered for `RETRY_AFTER` seconds, so an offline session fails fast instead
  of retrying on every render.
- **Opt-out.** `ART_NO_DOWNLOAD=1` disables it. The test suite sets it
  (`tests/_hermetic.py`); tests that exercise the download clear it and inject a
  fetcher, so nothing here touches the network in the suite.

An engine the user installed (on PATH, or next to the Python executable) always wins:
`agents/formatter.py::_find_latex_engine` checks the provisioned path last.

Tectonic itself downloads its TeX bundle on the first compile; that is Tectonic's own
network use, outside ART's Python. The `art-setup` skill warms it with one render.

**This module, the Jev client and the GitHub client are the only network clients under
`harness/`** (`tests/test_harness_boundary.py`). Nothing here is a generative call.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import sys
import tarfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable, Dict, NamedTuple, Optional, Tuple
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

TECTONIC_VERSION = "0.16.9"
RELEASE_URL = ("https://github.com/tectonic-typesetting/tectonic/releases/download/"
               f"tectonic%40{TECTONIC_VERSION}/")
# (system, machine) -> (release asset, SHA-256). From the `tectonic@0.16.9` release on
# GitHub: each asset downloaded and hashed, and checked against the digest GitHub's
# release API reports for it. Linux uses the static musl builds.
ASSETS: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("darwin", "aarch64"): ("tectonic-0.16.9-aarch64-apple-darwin.tar.gz",
                            "edb67c61aba768289f6da441c9e6f523cfaff4f8b2a5708523ef29c543f8e88e"),
    ("darwin", "x86_64"): ("tectonic-0.16.9-x86_64-apple-darwin.tar.gz",
                           "79d8839fa3594bfea9b2bf2ac0a0455bcc4d0de956a5e5c403107e9a72f79e86"),
    ("windows", "x86_64"): ("tectonic-0.16.9-x86_64-pc-windows-msvc.zip",
                            "131a24604785a9600989a3d91225f597df52ac06f00aeffe86fd529f99ee5cdd"),
    ("linux", "x86_64"): ("tectonic-0.16.9-x86_64-unknown-linux-musl.tar.gz",
                          "60b13a0826ae7ad9ce34b4a2df06bff2cfcfa6dda8a915477c0cbb84e1a4a902"),
    ("linux", "aarch64"): ("tectonic-0.16.9-aarch64-unknown-linux-musl.tar.gz",
                           "f9aa39017dbd51f111fdb93dda222178cbe51c8193508fc567b523cc74fff9c1"),
}
# Hosts a release download may redirect to (GitHub serves assets from its CDN).
ALLOWED_HOSTS = ("github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com")
OPT_OUT_ENV = "ART_NO_DOWNLOAD"
EXE_NAME = "tectonic.exe" if os.name == "nt" else "tectonic"
TIMEOUT = 20.0                # seconds per connect / read: offline fails at once
MAX_BYTES = 64 * 1024 * 1024  # the largest pinned asset is ~21 MB
MAX_REDIRECTS = 5
LOCK_WAIT = 600.0             # how long a second process waits for the first download
LOCK_STALE = 900.0            # a lock this old is a crashed download's leftover
RETRY_AFTER = 300.0           # a failure is reported, not retried, for this long

# A fetcher writes the body at `url` into `out`. Tests inject a fake one.
Fetcher = Callable[[str, BinaryIO], None]


class Provision(NamedTuple):
    path: Optional[str]
    reason: Optional[str] = None


class ToolchainError(Exception):
    """A download step failed; the message is the reason the user sees."""


_LOCK = threading.Lock()
_FAILED: Dict[str, Tuple[float, str]] = {}   # asset -> (monotonic time, reason)


def bin_dir() -> Path:
    from config import APP_DATA_DIR
    return Path(APP_DATA_DIR) / "bin"


def engine_path() -> Path:
    """Where the provisioned Tectonic lives: `$ART_DATA_DIR/bin/tectonic[.exe]`."""
    return bin_dir() / EXE_NAME


def platform_key() -> Tuple[str, str]:
    """`(system, machine)` normalised to `ASSETS`' spelling."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    return system, machine


def download_disabled() -> bool:
    return os.environ.get(OPT_OUT_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _host_allowed(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and (parts.hostname or "").lower() in ALLOWED_HOSTS


def _default_fetch(url: str, out: BinaryIO) -> None:
    """GET a pinned release URL into `out`, following redirects only to GitHub's hosts."""
    import requests

    for _ in range(MAX_REDIRECTS + 1):
        if not _host_allowed(url):
            raise ToolchainError(f"refused to fetch from {urlsplit(url).hostname or url}")
        with requests.get(url, stream=True, timeout=TIMEOUT, allow_redirects=False,
                          headers={"User-Agent": "art-mcp"}) as resp:
            if resp.is_redirect:
                url = requests.compat.urljoin(url, resp.headers["Location"])
                continue
            if resp.status_code != 200:
                raise ToolchainError(f"GitHub answered HTTP {resp.status_code}")
            written = 0
            for chunk in resp.iter_content(1 << 20):
                written += len(chunk)
                if written > MAX_BYTES:
                    raise ToolchainError("download larger than expected")
                out.write(chunk)
            return
    raise ToolchainError("too many redirects")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _extract(archive: Path, asset: str, dest: Path) -> None:
    """Copy the archive's one `tectonic[.exe]` member to `dest`. Member paths are
    matched by base name and never used as a destination."""
    def copy(src: BinaryIO) -> None:
        with open(dest, "wb") as out:
            while True:
                block = src.read(1 << 20)
                if not block:
                    break
                out.write(block)

    if asset.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if not info.is_dir() and PurePosixPath(info.filename).name == EXE_NAME:
                    with zf.open(info) as src:
                        copy(src)
                    return
    elif asset.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive, "r:gz") as tf:
            for member in tf.getmembers():
                if member.isfile() and PurePosixPath(member.name).name == EXE_NAME:
                    src = tf.extractfile(member)
                    if src is not None:
                        with src:
                            copy(src)
                        return
    else:
        raise ToolchainError(f"unknown archive type: {asset}")
    raise ToolchainError(f"{asset} holds no {EXE_NAME}")


def _acquire(lock: Path, target: Path) -> bool:
    """Take the download lock. False when another process held it and its engine is
    now in place (nothing left to do). Raises ToolchainError on a timeout."""
    deadline = time.monotonic() + LOCK_WAIT
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if target.is_file():
                return False
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE:
                    lock.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise ToolchainError("another download of Tectonic is still running")
            time.sleep(0.5)
            continue
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        return True


def _clear_engine_cache() -> None:
    """A fresh engine changes `engine_id()` (and so the render cache's template
    hash); drop its cached 'none'. Only if the module is loaded already."""
    rc = sys.modules.get("harness.render_cache")
    if rc is not None:
        rc.engine_id.cache_clear()


def _download(asset: str, sha256: str, fetch: Fetcher, target: Path) -> None:
    directory = target.parent
    part = directory / f".{asset}.part"
    tmp = directory / f".{EXE_NAME}.tmp"
    try:
        with open(part, "wb") as out:
            fetch(RELEASE_URL + asset, out)
        got = _sha256(part)
        if got != sha256:
            raise ToolchainError(f"checksum mismatch for {asset} (got {got[:12]}..., "
                                 f"expected {sha256[:12]}...); refused")
        _extract(part, asset, tmp)
        os.chmod(tmp, 0o755)
        os.replace(tmp, target)
    finally:
        for leftover in (part, tmp):
            try:
                leftover.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:   # e.g. a virus scanner holding it on Windows
                log.warning("could not remove %s: %s", leftover, exc)


def ensure_tectonic(*, fetch: Optional[Fetcher] = None) -> Provision:
    """The provisioned Tectonic's path, downloading it first if needed. Never raises."""
    try:
        target = engine_path()
        if target.is_file():
            return Provision(str(target))
        if download_disabled():
            return Provision(None, f"automatic download is off ({OPT_OUT_ENV}=1)")
        system, machine = platform_key()
        pinned = ASSETS.get((system, machine))
        if pinned is None:
            return Provision(None, f"no Tectonic {TECTONIC_VERSION} build is pinned for "
                                   f"{system}/{machine}")
        asset, sha256 = pinned
        failed = _FAILED.get(asset)
        if failed and time.monotonic() - failed[0] < RETRY_AFTER:
            return Provision(None, failed[1])

        with _LOCK:
            if target.is_file():
                return Provision(str(target))
            target.parent.mkdir(parents=True, exist_ok=True)
            lock = target.parent / ".tectonic.lock"
            if not _acquire(lock, target):
                _clear_engine_cache()
                return Provision(str(target))
            try:
                if not target.is_file():
                    log.info("downloading Tectonic %s (%s)", TECTONIC_VERSION, asset)
                    _download(asset, sha256, fetch or _default_fetch, target)
            finally:
                try:
                    lock.unlink()
                except FileNotFoundError:
                    pass
        _FAILED.pop(asset, None)
        _clear_engine_cache()
        return Provision(str(target))
    except Exception as exc:  # noqa: BLE001 - never raise into a tool
        reason = f"downloading Tectonic {TECTONIC_VERSION} failed: {str(exc)[:300] or type(exc).__name__}"
        log.warning("%s", reason)
        try:
            _FAILED[ASSETS[platform_key()][0]] = (time.monotonic(), reason)
        except Exception:  # noqa: BLE001
            pass
        return Provision(None, reason)


def latex_engine(*, fetch: Optional[Fetcher] = None) -> Tuple[Optional[Tuple[str, str]], Optional[str]]:
    """`((name, path), None)` for the engine to use, provisioning Tectonic when there is
    none; `(None, reason)` when there is still none."""
    from agents import formatter

    found = formatter._find_latex_engine()
    if found:
        return found, None
    got = ensure_tectonic(fetch=fetch)
    if got.path:
        return ("tectonic", got.path), None
    return None, got.reason
