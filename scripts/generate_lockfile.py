"""Generate requirements-lock.txt from the current venv via pip freeze.

Usage (from repo root with the venv to freeze active):
    python scripts/generate_lockfile.py

Regenerate from a clean venv, not a dev one, so editors, debuggers and leftovers
from removed features do not leak into the lock (#210):

    python -m venv .lockvenv
    .lockvenv/bin/pip install -r requirements-full.txt -c requirements-lock.txt pytest==9.0.3
    .lockvenv/bin/python scripts/generate_lockfile.py

`-c requirements-lock.txt` keeps every pin the lock already has and resolves only
what is new. If a pin conflicts with a newer requirement (the resolver names it),
drop that one line from a copy of the lock and pass the copy to `-c`.

CI and the Dockerfile install `requirements-core.txt` constrained by this file, and
`scripts/check_lock_drift.py` (run by CI and tests/test_lock_drift.py) fails when a
core requirement is missing from it or pinned outside its range.

The generated file pins every transitive dependency at its exact version.
Windows-only packages are annotated with a platform marker so the lockfile
is safe to use on Linux (e.g. inside Docker) without modification.
"""

import datetime
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCKFILE = ROOT / "requirements-lock.txt"

HEADER = """\
# requirements-lock.txt — generated {date} on {platform}
# DO NOT edit manually. Regenerate with scripts/generate_lockfile.py, from a clean
# venv (see its docstring for the three commands).
#
# Windows-only packages are annotated with "; sys_platform == \\"win32\\""
# so this file is safe to use on Linux (e.g. Docker) without modification.
#
# Dev environment:  pip install -r requirements-full.txt -c requirements-lock.txt
# CI and Docker:    pip install -r requirements-core.txt -c requirements-lock.txt
#
"""

# Packages that only exist / install on Windows.
# These are annotated with a platform marker rather than removed,
# so pip on Linux simply skips them instead of erroring out.
WINDOWS_ONLY = {"pywin32"}

result = subprocess.run(
    [sys.executable, "-m", "pip", "freeze"],
    capture_output=True,
    text=True,
    check=True,
)

lines = []
for line in result.stdout.splitlines():
    if "==" in line:
        pkg_name = line.split("==")[0].lower().replace("-", "_")
        if pkg_name in WINDOWS_ONLY:
            line += ' ; sys_platform == "win32"'
    lines.append(line)

header = HEADER.format(
    date=datetime.date.today().isoformat(),
    platform=sys.platform,
)
LOCKFILE.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
print(f"Written {LOCKFILE} ({len(lines)} packages)")
