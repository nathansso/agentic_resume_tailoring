"""Semantic vs literal coverage on the scripted host's pages (issue #126): replayed offline from recordings.

`eval/coverage_labels/benchmark_recordings.json` holds the real Jev answers to `requirement_covered@v1` for
every (bullet, requirement) of the benchmark pages, recorded once. Nothing here calls the API.
"""

import json
import subprocess
import sys
from pathlib import Path

from eval import coverage_benchmark as bench
from harness.decisions.coverage import LEGACY_VERSION

ROOT = Path(__file__).resolve().parent.parent


def test_the_benchmark_recordings_replay_at_a_full_hit_rate_and_the_committed_report_is_current(tmp_path):
    doc = json.loads(bench.RECORDINGS_PATH.read_text(encoding="utf-8"))
    assert {d["question_version"] for d in doc["decisions"]} == {LEGACY_VERSION}     # recorded before v2 and education
    assert {d["point"] for d in doc["decisions"]} == {"requirement_covered"}
    report = tmp_path / "BENCHMARK.md"
    proc = subprocess.run([sys.executable, str(ROOT / "eval" / "coverage_benchmark.py"), "analyze", "--report", str(report)],
                          capture_output=True, text=True, timeout=600, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    assert "hit rate 100%" in proc.stdout
    assert report.read_text(encoding="utf-8") == bench.REPORT_PATH.read_text(encoding="utf-8")
    assert len(bench.TASK_IDS) >= 5
