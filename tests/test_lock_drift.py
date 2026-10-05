"""CI and Docker install requirements-core.txt held to the lock (issue #210).

`pip install -r requirements-core.txt -c requirements-lock.txt` pins whatever the
lock lists and ignores the rest. So a core requirement the lock lacks installs at
the latest release, silently, which is how sqlmodel 0.0.47 turned CI red (#209).
`scripts/check_lock_drift.py` is the guard; these tests pin it and the places that
must use the constraint. The function is tested, not CI itself.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_lock_drift as drift  # noqa: E402

LOCK = "\n".join([
    "# header", "",
    "sqlmodel==0.0.47", "pydantic==2.12.5", "uvicorn==0.54.0", "python-jose==3.5.0",
    "pywin32==311 ; sys_platform == \"win32\"", "",
])


def _problems(core: str, lock: str = LOCK):
    return drift.lock_drift(drift.parse_requirements(core), lock, "core")


def test_the_repo_is_in_step_now():
    assert drift.check() == []


def test_bare_names_and_matching_pins_pass():
    assert _problems("sqlmodel\npydantic\nsqlmodel==0.0.47\npydantic>=2.11,<3\n") == []


def test_extras_comments_options_and_name_spelling_are_handled():
    core = "# a comment\n-r other.txt\nuvicorn[standard]\nPython_Jose[cryptography]  # trailing\n\n"
    assert _problems(core) == []


def test_a_package_the_lock_lacks_fails():
    (problem,) = _problems("sqlmodel\nsupabase\n")
    assert "supabase" in problem and "not in requirements-lock.txt" in problem


def test_a_pin_the_lock_does_not_satisfy_fails():
    (problem,) = _problems("sqlmodel==0.0.38\n")
    assert "sqlmodel" in problem and "0.0.47" in problem
    assert _problems("pydantic>=2.13\n")


def test_a_lock_line_that_is_not_an_exact_pin_fails():
    problems = _problems("sqlmodel\n", lock="sqlmodel>=0.0.47\n")
    assert any("not an exact pin" in p for p in problems)
    assert any("sqlmodel" in p and "not in requirements-lock.txt" in p for p in problems)


def test_a_platform_marker_on_the_lock_line_still_counts_and_an_excluded_marker_is_skipped():
    assert _problems("pywin32==311\n") == []
    assert _problems('somepkg ; python_version < "3"\n') == []


def test_the_pyproject_dependencies_are_checked_too():
    toml = '[project]\nname = "x"\ndependencies = ["sqlmodel==0.0.38", "pydantic>=2.7", "absent>=1"]\n'
    problems = drift.lock_drift(drift.pyproject_requirements(toml), LOCK, "pyproject.toml dependencies")
    assert len(problems) == 2
    assert any("sqlmodel" in p for p in problems) and any("absent" in p for p in problems)


def test_check_reports_a_deliberately_extra_core_package(tmp_path):
    core = tmp_path / "core.txt"
    lock = tmp_path / "lock.txt"
    core.write_text((ROOT / "requirements-core.txt").read_text(encoding="utf-8") + "\nleftpad-for-python\n",
                    encoding="utf-8")
    lock.write_text((ROOT / "requirements-lock.txt").read_text(encoding="utf-8"), encoding="utf-8")
    (problem,) = drift.check(core, lock, tmp_path / "missing-pyproject.toml")
    assert "leftpad-for-python" in problem


def test_check_reports_a_core_pin_that_leaves_the_lock(tmp_path):
    core = tmp_path / "core.txt"
    lock = tmp_path / "lock.txt"
    text = (ROOT / "requirements-core.txt").read_text(encoding="utf-8")
    assert "mcp==2.2.0" in text
    core.write_text(text.replace("mcp==2.2.0", "mcp==9.9.9"), encoding="utf-8")
    lock.write_text((ROOT / "requirements-lock.txt").read_text(encoding="utf-8"), encoding="utf-8")
    problems = drift.check(core, lock, tmp_path / "missing-pyproject.toml")
    assert len(problems) == 1 and "mcp" in problems[0]


# ---------------------------------------------------------------------------
# The installs that must use the constraint
# ---------------------------------------------------------------------------

def test_the_dockerfile_installs_core_constrained_by_the_lock():
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "requirements-lock.txt" in docker.split("COPY requirements-core.txt", 1)[1].split("\n", 1)[0], (
        "the lock must be copied into the image before the install"
    )
    assert "pip install --no-cache-dir -r requirements-core.txt -c requirements-lock.txt" in docker


def test_ci_installs_core_constrained_by_the_lock_and_runs_the_drift_check():
    ci = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    installs = [ln for ln in ci.splitlines() if "pip install -r requirements-core.txt" in ln]
    assert installs, "the test job no longer installs requirements-core.txt"
    for line in installs:
        assert "-c requirements-lock.txt" in line, f"unconstrained core install: {line.strip()}"
    assert "scripts/check_lock_drift.py" in ci


@pytest.mark.parametrize("name", ["sqlmodel", "mcp"])
def test_hand_pinned_core_packages_equal_the_lock_exactly(name):
    """A pin written by hand in the core file must equal the lock's, not merely
    fall in a range the lock happens to satisfy."""
    import re

    lock = re.search(rf"^{name}==(\S+)", (ROOT / "requirements-lock.txt").read_text(encoding="utf-8"), re.M)
    core = re.search(rf"^{name}==(\S+)", (ROOT / "requirements-core.txt").read_text(encoding="utf-8"), re.M)
    assert lock and core and lock.group(1) == core.group(1)
