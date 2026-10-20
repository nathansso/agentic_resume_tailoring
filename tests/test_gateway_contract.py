"""The LinkedIn gateway (#354) stays in step with the code it was ported from and with
the privacy page that describes it. The gateway's own behaviour tests live in
`gateway/tests` and run in `.github/workflows/gateway.yml`."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GATEWAY = ROOT / "gateway"
PKG = GATEWAY / "artie_gateway"


@pytest.fixture
def gw(monkeypatch):
    monkeypatch.syspath_prepend(str(GATEWAY))
    import importlib

    import artie_gateway  # noqa: F401

    return importlib


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_gateway_imports_nothing_from_the_rest_of_the_repo():
    """It deploys alone: only the standard library and its own package."""
    banned = {"ingestion", "harness", "config", "agents", "database", "web", "llm", "services",
              "langchain_core", "langchain", "requests", "sqlmodel", "fastapi", "anthropic", "openai"}
    for path in [*PKG.glob("*.py"), *(GATEWAY / "api").rglob("*.py")]:
        for line in _read(path).splitlines():
            m = re.match(r"\s*(?:from|import)\s+([A-Za-z_][\w]*)", line)
            if m and not line.lstrip().startswith("from ."):
                assert m.group(1) not in banned, f"{path.relative_to(ROOT)}: {line.strip()}"


def test_the_harness_does_not_import_the_gateway_package():
    for path in (ROOT / "harness").rglob("*.py"):
        assert "artie_gateway" not in _read(path), path


def test_url_normalization_matches_the_ingestors_output_form(gw):
    from artie_gateway.urls import normalize_profile_url
    from ingestion.linkedin import LinkedInIngestor

    for url in (
        "https://www.linkedin.com/in/jane-example",
        "https://www.linkedin.com/in/jane-example/",
        "https://www.linkedin.com/in/john-q-public-123",
    ):
        assert normalize_profile_url(url) == LinkedInIngestor._normalize_url(url).rstrip("/")


def test_the_bright_data_request_is_the_one_the_ingestor_makes(gw):
    from artie_gateway import brightdata

    import ingestion.linkedin as legacy

    assert brightdata.BRIGHTDATA_BASE == legacy._BRIGHTDATA_BASE
    source = _read(ROOT / "ingestion" / "linkedin.py")
    for fragment in ('"format": "json"', '"include_errors": "true"', 'json=[{"url": profile_url}]',
                     'f"{_BRIGHTDATA_BASE}/scrape"'):
        assert fragment in source
    gateway_source = _read(PKG / "brightdata.py")
    for fragment in ('"format": "json"', '"include_errors": "true"', '[{"url": profile_url}]', "/scrape?"):
        assert fragment in gateway_source


def test_the_default_dataset_id_is_the_apps(gw):
    from artie_gateway.config import DEFAULT_DATASET_ID

    config_source = _read(ROOT / "config.py")
    assert re.search(r'"BRIGHTDATA_LINKEDIN_DATASET_ID",\s*"%s"' % DEFAULT_DATASET_ID, config_source)


def test_every_environment_variable_is_read_by_the_code_and_documented():
    readme = _read(GATEWAY / "README.md")
    code = _read(PKG / "config.py")
    names = [
        "BRIGHTDATA_API_KEY", "BRIGHTDATA_LINKEDIN_DATASET_ID", "KV_REST_API_URL", "KV_REST_API_TOKEN",
        "ART_GATEWAY_DAILY_QUOTA", "ART_GATEWAY_MONTHLY_CAP_USD", "ART_GATEWAY_COST_PER_RECORD_USD",
        "ART_GATEWAY_IP_PER_MINUTE",
    ]
    for name in names:
        assert name in code, name
        assert name in readme, name


def test_the_gateway_is_not_packaged_in_the_wheel_or_sdist():
    pyproject = tomllib.loads(_read(ROOT / "pyproject.toml"))
    for kind in ("wheel", "sdist"):
        target = pyproject["tool"]["hatch"]["build"]["targets"][kind]
        assert not any(p.split("/")[0] in {"gateway", "artie_gateway"} for p in target["only-include"]), kind


def test_vercel_serves_the_documented_path():
    import json

    config = json.loads(_read(GATEWAY / "vercel.json"))
    assert {"source": "/v1/linkedin", "destination": "/api/v1/linkedin"} in config["rewrites"]
    assert (GATEWAY / "api" / "v1" / "linkedin.py").is_file()


def test_the_gateway_has_its_own_ci_job():
    workflow = _read(ROOT / ".github" / "workflows" / "gateway.yml")
    assert "gateway/**" in workflow and "pytest" in workflow


# --- the privacy page ----------------------------------------------------------------------

PRIVACY = ROOT / "site" / "src" / "pages" / "privacy.astro"


def test_the_privacy_page_exists_and_the_footer_links_it():
    assert PRIVACY.is_file()
    layout = _read(ROOT / "site" / "src" / "layouts" / "Base.astro")
    footer = layout[layout.index("<footer"):]
    assert 'href="/privacy"' in footer and ">Privacy<" in footer


def test_the_privacy_page_covers_what_the_issue_requires():
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _read(PRIVACY))).lower()
    for phrase in (
        "github user id",             # what the counters are keyed by
        "profile url",                # what passes through
        "github token",
        "counters",                   # what is kept
        "expire",                     # retention
        "prohibit scraping",          # LinkedIn's terms
        "your own profile",
        "data export",                # the alternative
        "bright data",
    ):
        assert phrase in text, f"privacy page does not say {phrase!r}"


def test_the_privacy_page_does_not_say_github_returns_nothing_else():
    """GitHub's answer carries public profile details too; the page says the service keeps none
    of them, not that it reads nothing else. (#355 dropped the "coming soon" notice beside it.)"""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _read(PRIVACY))).lower()
    assert "reads nothing else from your github account" not in text
    assert "coming soon" not in text and "isn't available yet" not in text


def test_the_privacy_page_claims_match_the_code():
    """The page states retention periods; the code must use them."""
    service = _read(PKG / "service.py")
    github = _read(PKG / "github.py")
    assert "self.store.incr(f\"ip:{digest}:{minute}\", 120)" in service        # two minutes
    assert "2 * 86400" in service                                              # about two days
    assert "45 * 86400" in service                                             # about six weeks
    assert "_cache_seconds" in github and "ART_GATEWAY_AUTH_CACHE_SECONDS\", \"300\"" in _read(PKG / "config.py")
    assert "sha256" in service and "sha256" in github                          # hashed address, hashed token
