"""Run the tailoring benchmark across several profiles and merge the results.

Issue #172. The benchmark measured one candidate, so every metric was a single
pooled number and "weak on career-changers" was not expressible. This runs the
same harness once per profile and reports the strata side by side.

**One subprocess per profile, and that is forced rather than chosen.**
`database/db.py` creates `engine = create_engine(DATABASE_URL)` at *import*
time, from a `config.DATABASE_URL` that is itself read at import. A second
`run_benchmark()` call inside one process therefore keeps writing to the first
run's throwaway database, however carefully the environment is rebound. A fresh
interpreter is the only place a fresh engine can come from.

Three further properties fall out of that choice, all of them wanted:

* **Cassette scope stays the task id, one cassette per profile.**
  `default_cassette_path(profile.stem, …)` was already per-profile. Keying a
  single cassette on a profile x task grid would instead re-record every profile
  whenever one was added.
* **`_FIXTURE` stays trivially correct.** It is a process global that
  `bind_fixture` rebinds; with one profile per process there is nothing to
  rebind, and the lazy default the notebook and tests rely on is untouched.
* **`run_benchmark`'s contract is unchanged**, so every existing caller, test
  and committed cassette keeps working.

The cost is process startup per profile, which is irrelevant beside a benchmark
run.

    python eval/benchmark_suite.py --mode plumbing --limit 10
    python eval/benchmark_suite.py --profiles eval/profiles/a.md eval/profiles/b.md
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.profile_meta import load_meta  # noqa: E402
from eval.profile_ontology import REPORTED_STRATA  # noqa: E402
from eval.tailoring_benchmark import (  # noqa: E402
    MODE_PLUMBING,
    MODES,
    PROFILES_DIR,
    RESULTS_DIR,
    STRATUM_KEYS,
    _aggregate,
    _aggregate_by_stratum,
    _mode_banner,
)

BENCHMARK = ROOT / "eval" / "tailoring_benchmark.py"


def discover_profiles(profiles_dir: Path = PROFILES_DIR) -> List[Path]:
    """Every profile markdown the suite reports on, in a stable order.

    Sorted on filename, which is content rather than filesystem order — a
    `glob` is not ordered, and an unordered iteration feeding a reported table
    is the bug class #158 and #171 each paid for.

    Profiles whose sidecar sets `"retired": true` are skipped. `#177` restricted
    the corpus to intern/entry postings and the original `benchmark_profile.md`
    is a four-year mid-level candidate, so pooling it into a per-stratum table
    would report an out-of-domain candidate under whatever slice it landed in.
    It is still runnable by name (`--profiles eval/profiles/benchmark_profile.md`),
    which is what the committed cassette and every historical figure need.
    """
    return [path for path in sorted(Path(profiles_dir).glob("*.md"))
            if not load_meta(path).retired]


def run_profile(profile: Path, out_dir: Path, mode: str, limit: int,
                tasks: Optional[List[str]] = None,
                timeout: int = 7200) -> Dict:
    """One profile, in its own interpreter. Returns its parsed results JSON."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(BENCHMARK), "--mode", mode,
           "--profile", str(profile), "--out", str(out_dir)]
    if limit:
        cmd += ["--limit", str(limit)]
    if tasks:
        cmd += ["--tasks", *tasks]

    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          timeout=timeout)
    produced = sorted(out_dir.glob("tailoring_benchmark_*.json"))
    if proc.returncode != 0 or not produced:
        # The child's stderr is the only place the real cause appears — a
        # cassette miss, a malformed sidecar, a failed ingest — so it is
        # surfaced rather than reduced to a return code.
        raise RuntimeError(
            f"{profile.name} failed (exit {proc.returncode}):\n"
            f"{proc.stderr[-3000:]}")
    return json.loads(produced[-1].read_text(encoding="utf-8"))


def merge(runs: List[Dict]) -> Dict:
    """Per-profile results → one suite result.

    Each task row is tagged with the strata of the profile that produced it, so
    a candidate axis (`breadth`, `evidence_density`, …) and a posting axis
    (`role_family`, `level`) slice the same rows through one code path.

    The pooled figure is kept, as everywhere else in this harness: it is what
    every historical CHANGELOG table is, and dropping it would break
    comparability with everything already recorded.
    """
    tagged: List[Dict] = []
    per_profile: Dict[str, Dict] = {}
    for run in runs:
        strata = run.get("profile_strata") or {}
        ok = [t for t in run["task_results"] if "error" not in t]
        for task in ok:
            row = dict(task)
            row["profile"] = run["profile"]
            # Candidate axes are namespaced so a profile stratum can never
            # silently collide with a JD one — both sides define `role_family`,
            # and pooling them would be meaningless.
            for axis, value in strata.items():
                row[f"profile_{axis}"] = value
            tagged.append(row)
        per_profile[run["profile"]] = {
            "strata": strata,
            "tasks": len(ok),
            "failed": run.get("failed") or [],
            "aggregate": run["aggregate"],
        }

    profile_axes = tuple(f"profile_{axis}" for axis in REPORTED_STRATA)
    return {
        "profiles": len(runs),
        "tasks": len(tagged),
        "per_profile": per_profile,
        "aggregate": _aggregate(tagged),
        "aggregate_by_stratum": _aggregate_by_stratum(
            tagged, axes=tuple(STRATUM_KEYS) + profile_axes),
    }


def run_suite(profiles: Optional[List[Path]] = None, mode: str = MODE_PLUMBING,
              limit: int = 0, tasks: Optional[List[str]] = None,
              out_dir: Path = RESULTS_DIR, verbose: bool = True) -> Dict:
    profiles = list(profiles) if profiles else discover_profiles()
    if not profiles:
        raise SystemExit(f"No profiles found in {PROFILES_DIR}")

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    suite_dir = Path(out_dir) / f"suite_{ts}"
    if verbose:
        print(_mode_banner(mode), flush=True)
        print(f"  profiles: {len(profiles)}\n", flush=True)

    runs = []
    for i, profile in enumerate(profiles, 1):
        if verbose:
            print(f"[{i}/{len(profiles)}] {profile.name} ...", flush=True)
        runs.append(run_profile(profile, suite_dir / profile.stem, mode, limit,
                                tasks))

    results = merge(runs)
    results.update({"timestamp": ts, "mode": mode})
    suite_dir.mkdir(parents=True, exist_ok=True)
    path = suite_dir / "suite.json"
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    if verbose:
        print(f"\nSuite results → {path}")
        print(_render_table(results))
        print(_mode_banner(mode))
    return results


def _render_table(results: Dict, metric: str = "ats_delta") -> str:
    """The per-stratum table, which is the point of the whole suite."""
    lines = [f"\n{metric} by stratum:"]
    for axis, buckets in results["aggregate_by_stratum"].items():
        lines.append(f"  {axis}:")
        for value, agg in buckets.items():
            stat = agg.get(metric)
            shown = stat["mean"] if stat else "—"
            lines.append(f"    {value:<28} n={agg['tasks']:<4} {shown}")
    pooled = results["aggregate"].get(metric)
    lines.append(f"  pooled: {pooled['mean'] if pooled else '—'} "
                 f"over {results['tasks']} task-runs")
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=MODES, default=MODE_PLUMBING)
    ap.add_argument("--profiles", nargs="*", type=Path, default=None,
                    help="profile markdown files (default: every one in eval/profiles/)")
    ap.add_argument("--tasks", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", type=Path, default=RESULTS_DIR)
    args = ap.parse_args()

    results = run_suite(profiles=args.profiles, mode=args.mode, limit=args.limit,
                        tasks=args.tasks, out_dir=args.out)
    failed = [p for p, r in results["per_profile"].items() if r["failed"]]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
