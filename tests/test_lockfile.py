"""Structural tests for requirements-lock.txt.

These tests are purely file-parsing — no network, no heavy imports.
They ensure the lockfile exists, is syntactically valid, covers all
direct deps from requirements.txt, and correctly annotates Windows-only
packages with platform markers so Linux Docker builds don't fail.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCKFILE = ROOT / "requirements-lock.txt"
REQUIREMENTS = ROOT / "requirements.txt"

# Valid lockfile line: name==version optionally followed by ; <marker>
LINE_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*)==\S+(\s*;\s*.+)?$")


def _normalized(name: str) -> str:
    """Normalize package name for comparison (lowercase, hyphens → underscores)."""
    return name.strip().lower().replace("-", "_")


def _parse_lockfile_packages(path: Path) -> list[tuple[str, str]]:
    """Return list of (normalized_name, version) for non-comment lines."""
    packages = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip environment marker before splitting on ==
        line_no_marker = line.split(";")[0].strip()
        if "==" in line_no_marker:
            name, version = line_no_marker.split("==", 1)
            packages.append((_normalized(name), version.strip()))
    return packages


def test_lockfile_exists():
    """requirements-lock.txt must be present at the repo root."""
    assert LOCKFILE.exists(), (
        "requirements-lock.txt is missing. "
        "Regenerate with: python scripts/generate_lockfile.py"
    )


def test_lockfile_is_parseable():
    """Every non-comment line must match name==version [; marker] syntax."""
    for line in LOCKFILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assert LINE_RE.match(line), (
            f"Unparseable line in requirements-lock.txt: {line!r}\n"
            "Regenerate with: python scripts/generate_lockfile.py"
        )


def test_all_requirements_txt_packages_in_lockfile():
    """Every direct dep in requirements.txt must appear in the lockfile.

    Catches the case where someone adds a new package to requirements.txt
    but forgets to regenerate the lockfile.
    """
    lock_names = {name for name, _ in _parse_lockfile_packages(LOCKFILE)}
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pkg = _normalized(line)
        assert pkg in lock_names, (
            f"{line!r} from requirements.txt not found in requirements-lock.txt. "
            "Regenerate with: python scripts/generate_lockfile.py"
        )


def test_pywin32_has_platform_marker():
    """pywin32 must carry a Windows platform marker so Linux installs skip it."""
    for line in LOCKFILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("pywin32=="):
            assert 'sys_platform == "win32"' in stripped, (
                "pywin32 is missing its platform marker in requirements-lock.txt. "
                "This will cause Docker (Linux) builds to fail. "
                "Regenerate with: python scripts/generate_lockfile.py"
            )
            return
    # pywin32 might not be installed in a given environment (e.g. Linux CI);
    # that's fine — the marker check only applies when it's present.


def test_no_duplicate_packages():
    """No package name should appear more than once in the lockfile."""
    seen: set[str] = set()
    for name, _ in _parse_lockfile_packages(LOCKFILE):
        assert name not in seen, (
            f"Duplicate package in requirements-lock.txt: {name}. "
            "Regenerate with: python scripts/generate_lockfile.py"
        )
        seen.add(name)
