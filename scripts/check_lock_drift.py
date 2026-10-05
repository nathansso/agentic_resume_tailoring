"""Fail when requirements-core.txt has drifted from requirements-lock.txt (#210).

CI and the Dockerfile install `requirements-core.txt` held to the lock:

    pip install -r requirements-core.txt -c requirements-lock.txt

A constraint file only constrains the packages it lists. pip ignores it for any
other package, with no warning, so a core requirement the lock lacks installs at
whatever PyPI ships that day. That is how sqlmodel 0.0.47 turned every PR red
with no code change (#209). This check closes the gap: every package in the core
file, and every base dependency in pyproject.toml, must appear in the lock at a
version that satisfies the requirement.

Usage (repo root):  python scripts/check_lock_drift.py
Exit 0 when in step, 1 with one line per problem otherwise. Needs `packaging`.
"""

from __future__ import annotations

import pathlib
import sys
import tomllib
from typing import Dict, List

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORE = ROOT / "requirements-core.txt"
LOCK = ROOT / "requirements-lock.txt"
PYPROJECT = ROOT / "pyproject.toml"

REGENERATE = "regenerate it (see the docstring of scripts/generate_lockfile.py)"


def parse_lock(text: str) -> tuple[Dict[str, str], List[str]]:
    """`{canonical name: version}` for a lockfile, plus any malformed lines.

    The lock must pin exactly (`name==version`); a platform marker after `;` is
    ignored, so a Windows-only package still counts as present.
    """
    pins: Dict[str, str] = {}
    bad: List[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].split(";", 1)[0].strip()
        if not line:
            continue
        name, sep, version = line.partition("==")
        if not sep or not name.strip() or not version.strip():
            bad.append(raw.strip())
            continue
        pins[canonicalize_name(name.strip())] = version.strip()
    return pins, bad


def parse_requirements(text: str) -> List[Requirement]:
    """The requirements in a requirements file; comments, blanks and pip
    options (`-r`, `-c`, ...) are skipped."""
    reqs: List[Requirement] = []
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith(("#", "-")):
            continue
        reqs.append(Requirement(line))
    return reqs


def lock_drift(requirements: List[Requirement], lock_text: str, source: str) -> List[str]:
    """One message per *requirements* entry the lock does not cover."""
    pins, bad = parse_lock(lock_text)
    problems = [f"requirements-lock.txt: not an exact pin: {line!r}" for line in bad]
    for req in requirements:
        # A requirement whose marker excludes this platform installs nowhere
        # here, so the lock owes it nothing.
        if req.marker is not None and not req.marker.evaluate():
            continue
        name = canonicalize_name(req.name)
        pinned = pins.get(name)
        if pinned is None:
            problems.append(
                f"{source}: {req.name!r} is not in requirements-lock.txt, so CI and the "
                f"Docker image would install it unpinned; {REGENERATE}."
            )
        elif not req.specifier.contains(pinned, prereleases=True):
            problems.append(
                f"{source}: {req.name} {req.specifier} is not satisfied by the lock's "
                f"{req.name}=={pinned}; change one of them, or {REGENERATE}."
            )
    return problems


def pyproject_requirements(text: str) -> List[Requirement]:
    """`[project].dependencies` of a pyproject.toml (the default install)."""
    return [Requirement(d) for d in tomllib.loads(text).get("project", {}).get("dependencies", [])]


def check(core: pathlib.Path = CORE, lock: pathlib.Path = LOCK,
          pyproject: pathlib.Path = PYPROJECT) -> List[str]:
    lock_text = lock.read_text(encoding="utf-8")
    problems = lock_drift(parse_requirements(core.read_text(encoding="utf-8")),
                          lock_text, core.name)
    if pyproject.exists():
        problems += lock_drift(pyproject_requirements(pyproject.read_text(encoding="utf-8")),
                               lock_text, f"{pyproject.name} dependencies")
    return problems


def main() -> int:
    try:
        problems = check()
    except InvalidRequirement as exc:
        problems = [f"unparseable requirement: {exc}"]
    for problem in problems:
        print(f"DRIFT: {problem}", file=sys.stderr)
    if not problems:
        print("requirements-core.txt and pyproject.toml are covered by requirements-lock.txt")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
