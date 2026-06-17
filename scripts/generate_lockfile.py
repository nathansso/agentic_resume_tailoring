"""Generate requirements-lock.txt from the current venv via pip freeze.

Usage (from repo root with venv active):
    python scripts/generate_lockfile.py

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
# DO NOT edit manually. Regenerate with:
#   python scripts/generate_lockfile.py
#
# Windows-only packages are annotated with "; sys_platform == \\"win32\\""
# so this file is safe to use on Linux (e.g. Docker) without modification.
#
# Install:  pip install -r requirements-lock.txt
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
