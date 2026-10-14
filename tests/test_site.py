"""The landing site scaffold (issue #272): `site/`, an Astro + Tailwind static site.

The site is a separate project: its own `package.json` and lockfile, deployed by
Vercel's Git integration, and never part of the `art-mcp` package. These tests
hold the pieces that can silently rot from the Python side without a network or
an npm call: pinned dependencies, the Vercel config, the theme tokens staying in
step with the app's, and `site/` staying out of the wheel and sdist. CI's `site`
workflow (`.github/workflows/site.yml`) is what actually builds it.
"""

import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
APP_CSS = ROOT / "web" / "frontend" / "src" / "index.css"
SITE_CSS = SITE / "src" / "styles" / "global.css"
BASE_LAYOUT = SITE / "src" / "layouts" / "Base.astro"
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def _tokens(css: str, selector: str) -> dict[str, str]:
    """Custom properties declared in the first `selector { ... }` block."""
    match = re.search(rf"(?m)^\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
    assert match, f"{selector} block not found"
    return {
        name: " ".join(value.split())
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", match.group(1))
    }


# --------------------------------------------------------------- package.json


def test_site_dependencies_are_exact_pins_and_the_lockfile_is_committed():
    pkg = json.loads((SITE / "package.json").read_text(encoding="utf-8"))
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    assert {"astro", "tailwindcss", "@tailwindcss/vite"} <= set(deps)
    unpinned = {n: v for n, v in deps.items() if not EXACT_VERSION.match(v)}
    assert not unpinned, f"pin exact versions (no ^ or ~): {unpinned}"
    # Tailwind and its Vite plugin ship together; a split pair breaks the build.
    assert deps["tailwindcss"] == deps["@tailwindcss/vite"]
    assert (SITE / "package-lock.json").is_file(), "`npm ci` needs a committed lockfile"


def test_site_has_a_build_script_and_static_output():
    pkg = json.loads((SITE / "package.json").read_text(encoding="utf-8"))
    assert pkg["scripts"]["build"] == "astro build"
    config = (SITE / "astro.config.mjs").read_text(encoding="utf-8")
    assert re.search(r"output:\s*[\"']static[\"']", config)
    # Vercel serves at the root: a base path would break every asset URL.
    assert "base:" not in config


# ---------------------------------------------------------------- vercel.json


def test_vercel_config_is_astro():
    config = json.loads((SITE / "vercel.json").read_text(encoding="utf-8"))
    assert config["framework"] == "astro"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _ignore_step(cwd, previous_sha):
    """Run vercel.json's ignoreCommand the way Vercel does: from the root
    directory (site/), under sh. Exit 0 skips the build; anything else builds."""
    command = json.loads((SITE / "vercel.json").read_text(encoding="utf-8"))["ignoreCommand"]
    env = {**os.environ, "VERCEL_GIT_PREVIOUS_SHA": previous_sha}
    return subprocess.run([SH, "-c", command], cwd=cwd, env=env, capture_output=True).returncode


SH = shutil.which("sh")


@pytest.mark.skipif(SH is None or shutil.which("git") is None, reason="needs sh and git")
def test_vercel_ignore_step_compares_against_the_last_deploy(tmp_path):
    # The first version compared HEAD^ with HEAD, so the project's first
    # deploy was cancelled whenever main's latest commit didn't touch site/.
    repo = tmp_path / "repo"
    (repo / "site").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "site" / "index.html").write_text("v1")
    (repo / "other.py").write_text("v1")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "one")
    deployed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                              capture_output=True, text=True).stdout.strip()
    site = repo / "site"

    # No previous deploy (a new project or branch): build.
    assert _ignore_step(site, "") != 0

    # Only files outside site/ changed since the last deploy: skip.
    (repo / "other.py").write_text("v2")
    _git(repo, "commit", "-qam", "outside")
    assert _ignore_step(site, deployed) == 0

    # site/ changed since the last deploy, even several commits back: build.
    (site / "index.html").write_text("v2")
    _git(repo, "commit", "-qam", "site")
    (repo / "other.py").write_text("v3")
    _git(repo, "commit", "-qam", "outside again")
    assert _ignore_step(site, deployed) != 0

    # The last deploy's commit isn't in Vercel's shallow clone: build.
    assert _ignore_step(site, "0" * 40) != 0


# ----------------------------------------------------------------- the theme


def test_site_theme_tokens_match_the_app():
    app, site = APP_CSS.read_text(encoding="utf-8"), SITE_CSS.read_text(encoding="utf-8")
    for selector in (":root", ".dark"):
        app_tokens, site_tokens = _tokens(app, selector), _tokens(site, selector)
        assert {"--background", "--foreground", "--primary", "--primary-foreground"} <= set(
            site_tokens
        )
        drift = {
            name: (value, site_tokens.get(name))
            for name, value in app_tokens.items()
            if site_tokens.get(name) != value
        }
        assert not drift, f"{selector} drifted from web/frontend/src/index.css: {drift}"


def test_site_maps_the_tokens_into_tailwind_and_uses_the_dark_class():
    css = SITE_CSS.read_text(encoding="utf-8")
    assert "@theme inline" in css
    for name in ("background", "foreground", "primary", "primary-foreground", "border"):
        assert f"--color-{name}: hsl(var(--{name}));" in css
    assert re.search(r"@custom-variant dark \(&:where\(\.dark, \.dark \*\)\)", css)


def test_layout_sets_the_theme_before_first_paint_with_the_apps_storage_key():
    layout = BASE_LAYOUT.read_text(encoding="utf-8")
    head = layout.split("</head>", 1)[0]
    assert "is:inline" in head and "classList.add(\"dark\")" in head
    assert "prefers-color-scheme: dark" in head
    # Storage can throw (private mode): the script must swallow that.
    assert re.search(r"try\s*\{.*localStorage\.getItem.*\}\s*catch", head, re.S)
    # Same key as the app, so the two themes behave identically.
    app_key = re.search(r'THEME_KEY\s*=\s*"([^"]+)"', (ROOT / "web/frontend/src/lib/theme.ts").read_text(encoding="utf-8"))
    assert app_key and f'"{app_key.group(1)}"' in head


def test_layout_has_the_nav_links_the_issue_specifies():
    layout = BASE_LAYOUT.read_text(encoding="utf-8")
    for href in ('"/install"', '"/usage"', "https://github.com/nathansso/agentic_resume_tailoring"):
        assert href in layout
    for page in ("index", "install", "usage"):
        assert (SITE / "src" / "pages" / f"{page}.astro").is_file()


def test_home_hero_links_resolve():
    home = (SITE / "src" / "pages" / "index.astro").read_text(encoding="utf-8")
    assert 'href="/install"' in home
    assert 'href="#how-it-works"' in home and 'id="how-it-works"' in home


# ------------------------------------------------------- CI and packaging


def test_site_workflow_builds_site_in_its_own_file():
    workflow = (ROOT / ".github" / "workflows" / "site.yml").read_text(encoding="utf-8")
    assert "site/**" in workflow and "pull_request:" in workflow
    assert re.search(r'node-version:\s*"24"', workflow)
    assert "npm ci" in workflow and "npm run build" in workflow
    # A separate file keeps it out of the way of tests.yml.
    assert "site build" not in (ROOT / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8"
    )


def test_site_is_not_packaged_in_the_wheel_or_sdist():
    targets = PYPROJECT["tool"]["hatch"]["build"]["targets"]
    for kind in ("wheel", "sdist"):
        included = targets[kind]["only-include"]
        assert not any(p == "site" or p.startswith("site/") for p in included), kind
        assert "/site" in targets[kind]["exclude"], f"{kind} should exclude site/ explicitly"
    # site/ is not a Python package either.
    assert not (SITE / "__init__.py").exists()


def test_site_build_output_is_gitignored():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for entry in ("site/node_modules/", "site/dist/", "site/.astro/"):
        assert entry in ignored
