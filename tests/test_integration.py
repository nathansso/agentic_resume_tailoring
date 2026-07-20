"""End-to-end integration tests — require network and real DB. Skipped by default."""
import subprocess
import sys
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.mark.integration
@pytest.mark.slow
def test_full_cli_ingestion_and_tailor_writes_artifacts_to_cwd():
    """CLI-specific: `cli.py tailor` writes its artifacts to the process CWD.

    This asserts CLI behavior only — the commands below run with ``cwd=ROOT``,
    so the artifacts land in the repo root. The chat/web tailoring path
    deliberately writes nothing (issue #130); see
    ``test_run_tailor_writes_nothing_to_cwd`` in tests/test_chat.py.
    """
    py = sys.executable

    resume = str(FIXTURES / "sample_resume.md")
    job = str(FIXTURES / "sample_job.txt")

    steps = [
        [py, "cli.py", "ingest-resume", resume],
        [py, "cli.py", "ingest-github"],
        [py, "cli.py", "tailor", job],
    ]

    for cmd in steps:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        assert proc.returncode == 0, (
            f"Command failed: {' '.join(cmd)}\n"
            f"STDOUT:\n{proc.stdout[-2000:]}\n"
            f"STDERR:\n{proc.stderr[-2000:]}"
        )

    out_json = ROOT / "tailored_output.json"
    out_tex = ROOT / "tailored_resume.tex"

    assert out_json.exists() and out_json.stat().st_size > 0
    assert out_tex.exists() and out_tex.stat().st_size > 0
