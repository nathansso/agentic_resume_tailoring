"""The toolchain downloader (#384): render provisions a pinned Tectonic on first need.

No test here touches the network (`tests/_hermetic.py`): each builds a tiny `.zip` or
`.tar.gz` holding a stub `tectonic[.exe]`, pins its SHA-256 in place of the real one,
and serves it from a fake fetcher. The engine the stub stands in for is never run on
Windows (a stub cannot be an .exe); there, the compile is intercepted at
`subprocess.run`, which proves the provisioned path is the one invoked.
"""

import hashlib
import io
import os
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

import pytest
from sqlmodel import Session, select

from harness import toolchain

STUB = b"#!/bin/sh\necho 'Tectonic 0.16.9'\n"


def _zip(name=None, body=STUB) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("README.md", "not the engine")
        zf.writestr(name or toolchain.EXE_NAME, body)
    return buf.getvalue()


def _targz(name=None, body=STUB) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name or toolchain.EXE_NAME)
        info.size, info.mode = len(body), 0o644          # not executable in the archive
        tf.addfile(info, io.BytesIO(body))
    return buf.getvalue()


class FakeFetcher:
    def __init__(self, payload: bytes = b"", error: Exception = None):
        self.payload, self.error, self.urls = payload, error, []

    def __call__(self, url, out):
        self.urls.append(url)
        if self.error:
            raise self.error
        out.write(self.payload)


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """A data dir with no engine in it, downloads switched back on, no machine engine."""
    import config

    monkeypatch.setattr(config, "APP_DATA_DIR", tmp_path)
    monkeypatch.delenv(toolchain.OPT_OUT_ENV, raising=False)
    monkeypatch.setattr(toolchain, "_FAILED", {})
    # Hide any engine the developer has: none on PATH, none next to Python.
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    fake_py = tmp_path / "python" / "python.exe"
    fake_py.parent.mkdir()
    monkeypatch.setattr(sys, "executable", str(fake_py))
    return tmp_path


def _pin(monkeypatch, asset: str, payload: bytes, sha: str = None):
    monkeypatch.setattr(toolchain, "ASSETS", {
        toolchain.platform_key(): (asset, sha or hashlib.sha256(payload).hexdigest())})


@pytest.mark.parametrize("asset,build", [("tectonic-test.zip", _zip),
                                         ("tectonic-test.tar.gz", _targz)])
def test_a_verified_archive_is_extracted_marked_executable_and_found(data_dir, monkeypatch,
                                                                    asset, build):
    from agents.formatter import _find_latex_engine

    payload = build()
    _pin(monkeypatch, asset, payload)
    fetch = FakeFetcher(payload)
    assert _find_latex_engine() is None

    got = toolchain.ensure_tectonic(fetch=fetch)

    target = data_dir / "bin" / toolchain.EXE_NAME
    assert got == toolchain.Provision(str(target)) and target.read_bytes() == STUB
    assert fetch.urls == [toolchain.RELEASE_URL + asset]
    if os.name != "nt":
        assert os.access(target, os.X_OK)
    assert sorted(p.name for p in target.parent.iterdir()) == [toolchain.EXE_NAME]  # no leftovers
    assert _find_latex_engine() == ("tectonic", str(target))
    assert toolchain.ensure_tectonic(fetch=FakeFetcher(error=AssertionError("refetched"))).path


@pytest.mark.skipif(os.name == "nt", reason="the stub is a shell script")
def test_the_provisioned_stub_really_runs(data_dir, monkeypatch):
    payload = _targz()
    _pin(monkeypatch, "tectonic-test.tar.gz", payload)
    path = toolchain.ensure_tectonic(fetch=FakeFetcher(payload)).path
    out = subprocess.run([path, "--version"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "Tectonic 0.16.9"


def test_a_checksum_mismatch_is_refused_and_leaves_no_executable(data_dir, monkeypatch):
    payload = _zip()
    _pin(monkeypatch, "tectonic-test.zip", payload, sha="0" * 64)
    got = toolchain.ensure_tectonic(fetch=FakeFetcher(payload))
    assert got.path is None and "checksum mismatch" in got.reason and "refused" in got.reason
    bin_dir = data_dir / "bin"
    assert not (bin_dir / toolchain.EXE_NAME).exists()
    assert list(bin_dir.iterdir()) == []                      # the download was deleted too


def test_an_archive_without_the_engine_is_refused(data_dir, monkeypatch):
    payload = _zip(name="bin/other")
    _pin(monkeypatch, "tectonic-test.zip", payload)
    got = toolchain.ensure_tectonic(fetch=FakeFetcher(payload))
    assert got.path is None and "holds no" in got.reason
    assert list((data_dir / "bin").iterdir()) == []


def test_the_opt_out_returns_the_reason_without_fetching(data_dir, monkeypatch):
    monkeypatch.setenv(toolchain.OPT_OUT_ENV, "1")
    fetch = FakeFetcher(error=AssertionError("must not fetch"))
    got = toolchain.ensure_tectonic(fetch=fetch)
    assert got.path is None and "ART_NO_DOWNLOAD=1" in got.reason and fetch.urls == []


def test_a_failed_download_returns_the_reason_and_is_not_retried_at_once(data_dir, monkeypatch):
    payload = _zip()
    _pin(monkeypatch, "tectonic-test.zip", payload)
    offline = FakeFetcher(error=OSError("Name or service not known"))
    got = toolchain.ensure_tectonic(fetch=offline)
    assert got.path is None and "Name or service not known" in got.reason
    again = toolchain.ensure_tectonic(fetch=offline)
    assert again.reason == got.reason and len(offline.urls) == 1   # fails fast, no retry
    monkeypatch.setattr(toolchain, "RETRY_AFTER", 0.0)
    assert toolchain.ensure_tectonic(fetch=FakeFetcher(payload)).path   # later, it recovers


def test_an_unpinned_platform_is_reported_not_guessed(data_dir, monkeypatch):
    monkeypatch.setattr(toolchain, "platform_key", lambda: ("plan9", "mips"))
    got = toolchain.ensure_tectonic(fetch=FakeFetcher(error=AssertionError("must not fetch")))
    assert got.path is None and "plan9/mips" in got.reason


def test_a_held_lock_waits_and_a_stale_one_is_cleared(data_dir, monkeypatch):
    payload = _zip()
    _pin(monkeypatch, "tectonic-test.zip", payload)
    lock = data_dir / "bin" / ".tectonic.lock"
    lock.parent.mkdir()
    lock.write_text("123")
    monkeypatch.setattr(toolchain, "LOCK_WAIT", 0.0)
    busy = toolchain.ensure_tectonic(fetch=FakeFetcher(payload))
    assert busy.path is None and "still running" in busy.reason

    toolchain._FAILED.clear()
    old = time.time() - toolchain.LOCK_STALE - 60
    os.utime(lock, (old, old))
    assert toolchain.ensure_tectonic(fetch=FakeFetcher(payload)).path
    assert not lock.exists()


def test_an_engine_the_user_installed_wins_over_the_downloaded_one(data_dir, monkeypatch):
    from agents.formatter import _find_latex_engine

    provisioned = data_dir / "bin" / toolchain.EXE_NAME
    provisioned.parent.mkdir()
    provisioned.write_bytes(STUB)
    assert _find_latex_engine() == ("tectonic", str(provisioned))
    on_path = str(data_dir / "elsewhere" / "tectonic")
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: on_path if name == "tectonic" else None)
    assert _find_latex_engine() == ("tectonic", on_path)
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: "/usr/bin/pdflatex" if name == "pdflatex" else None)
    assert _find_latex_engine() == ("pdflatex", "/usr/bin/pdflatex")
    # And a machine engine means nothing is downloaded.
    engine, reason = toolchain.latex_engine(fetch=FakeFetcher(error=AssertionError("fetched")))
    assert engine == ("pdflatex", "/usr/bin/pdflatex") and reason is None


def test_provisioning_refreshes_the_cached_engine_id(data_dir, monkeypatch):
    from harness import render_cache

    payload = _zip()
    _pin(monkeypatch, "tectonic-test.zip", payload)
    render_cache.engine_id.cache_clear()
    try:
        assert render_cache.engine_id() == "none"
        none_hash = render_cache.template_hash()
        toolchain.ensure_tectonic(fetch=FakeFetcher(payload))
        assert render_cache.engine_id() != "none"
        assert render_cache.template_hash() != none_hash
    finally:
        render_cache.engine_id.cache_clear()


def test_the_same_tectonic_version_keeps_the_same_template_hash():
    from harness.render_cache import template_hash

    # The engine id is what `tectonic --version` prints, not where the binary lives,
    # so a downloaded 0.16.9 reuses rows a user-installed 0.16.9 measured.
    assert template_hash(engine="Tectonic 0.16.9") == template_hash(engine="Tectonic 0.16.9")
    assert template_hash(engine="Tectonic 0.16.9") != template_hash(engine="Tectonic 0.17.0")


def test_the_default_fetcher_only_reaches_github_over_https():
    assert toolchain._host_allowed(toolchain.RELEASE_URL + "x.zip")
    assert toolchain._host_allowed("https://release-assets.githubusercontent.com/a")
    assert not toolchain._host_allowed("http://github.com/x")
    assert not toolchain._host_allowed("https://evil.example/x")
    assert not toolchain._host_allowed("https://github.com.evil.example/x")
    with pytest.raises(toolchain.ToolchainError, match="refused"):
        toolchain._default_fetch("https://evil.example/tectonic.zip", io.BytesIO())


def test_every_pinned_asset_is_the_pinned_version_with_a_full_sha256():
    assert set(toolchain.ASSETS) == {("darwin", "aarch64"), ("darwin", "x86_64"),
                                     ("windows", "x86_64"), ("linux", "x86_64"),
                                     ("linux", "aarch64")}
    for asset, sha in toolchain.ASSETS.values():
        assert asset.startswith(f"tectonic-{toolchain.TECTONIC_VERSION}-")
        assert len(sha) == 64 and int(sha, 16) >= 0
    assert "musl" in toolchain.ASSETS[("linux", "x86_64")][0]


# ── the render tool, end to end ──────────────────────────────────────────────

CONTENT = {"experiences": [{"title": "Data Science Intern", "company": "IDX Exchange",
                            "start_date": "Jun 2024", "end_date": "Sep 2024",
                            "bullets": ["Built a gradient-boosted ensemble on 200K listings"]}],
           "projects": [], "skills_ranked": [{"name": "Python", "category": "Language"}]}


@pytest.fixture()
def job(kg, isolated_engine):
    from database.models import UserJobResult
    from harness import tree

    with Session(isolated_engine) as s:
        job_id = s.exec(select(UserJobResult).where(UserJobResult.user_id == kg)).first().job_id
    tree.commit_node(kg, job_id, content=CONTENT, source="host")
    return kg, str(job_id)


def _one_page_pdf() -> bytes:
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(612, 792)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _fake_engine(monkeypatch, engine: Path):
    """Stand in for the provisioned engine at `subprocess.run`: a compile writes a
    one-page PDF, a measurement writes one line per bullet to the log."""
    real_run, calls = subprocess.run, []

    def run(cmd, *args, **kwargs):
        if not cmd or str(cmd[0]) != str(engine):
            return real_run(cmd, *args, **kwargs)
        calls.append(list(cmd))
        text = kwargs.get("text")
        if "--version" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "Tectonic 0.16.9\n" if text else b"", "")
        outdir, tex = Path(cmd[cmd.index("--outdir") + 1]), Path(cmd[-1])
        if "--keep-logs" in cmd:
            n = tex.read_text(encoding="utf-8").count("ARTLINES:")
            (outdir / f"{tex.stem}.log").write_text(
                "\n".join(f"ARTLINES:{i}:1" for i in range(n)), encoding="utf-8")
        else:
            (outdir / f"{tex.stem}.pdf").write_bytes(_one_page_pdf())
        empty = "" if text else b""
        return subprocess.CompletedProcess(cmd, 0, empty, empty)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_the_first_render_on_a_machine_without_latex_downloads_and_makes_a_pdf(
        job, data_dir, monkeypatch):
    from harness import render_cache
    from harness.contract import invoke

    uid, job_id = job
    payload = _zip()
    _pin(monkeypatch, "tectonic-test.zip", payload)
    fetch = FakeFetcher(payload)
    monkeypatch.setattr(toolchain, "_default_fetch", fetch)
    engine = data_dir / "bin" / toolchain.EXE_NAME
    calls = _fake_engine(monkeypatch, engine)
    render_cache.engine_id.cache_clear()
    try:
        out = invoke("render", uid, {"job_id": job_id})
    finally:
        render_cache.engine_id.cache_clear()

    assert out["error"] is None and out["hint"] is None
    assert open(out["pdf_path"], "rb").read(4) == b"%PDF" and out["pages"] == 1
    assert len(fetch.urls) == 1 and engine.is_file()
    assert any("--keep-logs" in c for c in calls)            # it measured with it, too
    assert out["line_budget"].get("over_by") is not None


def test_with_downloads_off_render_returns_the_tex_and_says_why(job, data_dir, monkeypatch):
    from harness.contract import invoke

    uid, job_id = job
    monkeypatch.setenv(toolchain.OPT_OUT_ENV, "1")
    out = invoke("render", uid, {"job_id": job_id})
    assert out["pdf_path"] is None and out["tex_path"]
    assert out["hint"].startswith("No LaTeX engine found") and "ART_NO_DOWNLOAD=1" in out["hint"]


def test_a_failed_download_returns_the_no_engine_result_with_the_reason(job, data_dir,
                                                                       monkeypatch):
    from harness.contract import invoke

    uid, job_id = job
    payload = _zip()
    _pin(monkeypatch, "tectonic-test.zip", payload)
    monkeypatch.setattr(toolchain, "_default_fetch",
                        FakeFetcher(error=OSError("network is unreachable")))
    out = invoke("render", uid, {"job_id": job_id})
    assert out["error"] is None and out["pdf_path"] is None and out["tex_path"]
    assert "No LaTeX engine found" in out["hint"] and "network is unreachable" in out["hint"]
    assert not (data_dir / "bin" / toolchain.EXE_NAME).exists()
