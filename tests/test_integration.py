"""End-to-end integration tests — require network and real DB. Skipped by default."""
import subprocess
import sys
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.integration
@pytest.mark.slow
def test_full_cli_ingestion_and_tailor_pipeline():
    py = sys.executable

    steps = [
        [py, "cli.py", "ingest-resume", "Nathaniel Oliver Resume - 3_27_6.md"],
        [py, "cli.py", "ingest-github"],
        [py, "cli.py", "tailor", "test.txt"],
    ]

    for cmd in steps:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        assert proc.returncode == 0, (
            f"Command failed: {' '.join(cmd)}\n"
            f"STDOUT:\n{proc.stdout[-2000:]}\n"
            f"STDERR:\n{proc.stderr[-2000:]}"
        )

    out_json = ROOT / "tailored_output.json"
    out_md = ROOT / "tailored_resume.md"

    assert out_json.exists() and out_json.stat().st_size > 0
    assert out_md.exists() and out_md.stat().st_size > 0
