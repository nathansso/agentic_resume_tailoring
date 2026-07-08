"""Unit tests for GitHubIngestor's HTTP internals (issue #74).

Covers rate-limit detection and the tree-based import/dependency scan that
replaced the old unbounded per-directory recursive scan.
"""
import base64

import pytest

import ingestion.github as gh_module
from ingestion.github import GitHubIngestor, GitHubRateLimitError


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._json_data


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_get_raises_on_rate_limit(monkeypatch):
    """403 + X-RateLimit-Remaining: 0 raises GitHubRateLimitError."""
    monkeypatch.setattr(
        gh_module.requests, "get",
        lambda *a, **kw: FakeResponse(403, headers={"X-RateLimit-Remaining": "0"}),
    )
    instance = GitHubIngestor(username="owner", token=None)
    with pytest.raises(GitHubRateLimitError):
        instance._get("https://api.github.com/repos/owner/repo")


def test_get_does_not_raise_on_generic_403(monkeypatch):
    """A plain permission-denied 403 (no rate-limit header) is not misclassified."""
    monkeypatch.setattr(
        gh_module.requests, "get",
        lambda *a, **kw: FakeResponse(403, headers={}),
    )
    instance = GitHubIngestor(username="owner", token=None)
    resp = instance._get("https://api.github.com/repos/owner/repo")
    assert resp.status_code == 403


def test_get_does_not_raise_on_404(monkeypatch):
    monkeypatch.setattr(gh_module.requests, "get", lambda *a, **kw: FakeResponse(404))
    instance = GitHubIngestor(username="owner", token=None)
    resp = instance._get("https://api.github.com/repos/owner/repo")
    assert resp.status_code == 404


def test_fetch_repo_propagates_rate_limit(monkeypatch):
    """fetch_repo must not swallow a rate limit into a misleading None ('not found')."""
    monkeypatch.setattr(
        gh_module.requests, "get",
        lambda *a, **kw: FakeResponse(403, headers={"X-RateLimit-Remaining": "0"}),
    )
    with pytest.raises(GitHubRateLimitError):
        GitHubIngestor.fetch_repo("owner", "repo", token=None)


def test_import_scan_uses_single_tree_call_and_is_bounded(monkeypatch):
    """The tree-based scan makes exactly one tree call and stays bounded,
    unlike the old recursive per-directory/per-file scan."""
    tree_entries = [{"path": f"pkg/mod{i}.py", "type": "blob"} for i in range(40)]
    calls = {"tree": 0, "content": 0}

    def fake_get(url, headers=None, timeout=None):
        if "git/trees" in url:
            calls["tree"] += 1
            return FakeResponse(200, {"tree": tree_entries, "truncated": False})
        calls["content"] += 1
        return FakeResponse(200, {
            "content": _b64("import numpy\n"),
            "encoding": "base64",
        })

    monkeypatch.setattr(gh_module.requests, "get", fake_get)
    instance = GitHubIngestor(username="owner", token=None)

    tree = instance._fetch_tree("repo")
    assert calls["tree"] == 1

    imports = instance._extract_imports_from_repo("repo", tree)
    assert "numpy" in imports
    # 40 candidate files exist, but the scan must cap how many it fetches.
    assert calls["content"] <= gh_module.MAX_IMPORT_SCAN_FILES
    assert calls["tree"] == 1  # no extra tree calls triggered by the scan


def test_dependency_files_only_fetches_present_files(monkeypatch):
    """With a tree listing, only confirmed-present dependency files are fetched —
    not all 8 known filenames blindly."""
    tree = [
        {"path": "requirements.txt", "type": "blob"},
        {"path": "README.md", "type": "blob"},
        {"path": "src/app.py", "type": "blob"},
    ]
    fetched = []

    def fake_get(url, headers=None, timeout=None):
        if "git/trees" in url:
            return FakeResponse(200, {"tree": tree, "truncated": False})
        fetched.append(url)
        if url.endswith("/contents/requirements.txt"):
            return FakeResponse(200, {"content": _b64("flask\n"), "encoding": "base64"})
        return FakeResponse(200, {"content": _b64("import flask\n"), "encoding": "base64"})

    monkeypatch.setattr(gh_module.requests, "get", fake_get)
    instance = GitHubIngestor(username="owner", token=None)

    deps = instance._fetch_dependency_files("repo", tree)
    dep_fetch_urls = [u for u in fetched if "/contents/" in u and not u.endswith(".py")]
    assert len(dep_fetch_urls) == 1
    assert "requirements.txt" in deps


def test_dependency_files_falls_back_without_tree(monkeypatch):
    """When no tree is available (fetch failed / empty repo), fall back to
    checking all known dependency filenames so nothing regresses."""
    seen = []

    def fake_get(url, headers=None, timeout=None):
        seen.append(url)
        return FakeResponse(404)

    monkeypatch.setattr(gh_module.requests, "get", fake_get)
    instance = GitHubIngestor(username="owner", token=None)

    deps = instance._fetch_dependency_files("repo", tree=None)
    assert deps == {}
    dep_urls = [u for u in seen if "/contents/" in u]
    assert len(dep_urls) == len(gh_module.DEPENDENCY_FILES)
