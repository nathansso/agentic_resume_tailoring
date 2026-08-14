"""Re-fetch the body of every committed posting, in place.

    python scripts/repair_jd_bodies.py                 # dry run: report only
    python scripts/repair_jd_bodies.py --apply         # rewrite descriptions
    python scripts/repair_jd_bodies.py --apply --only truncated,html_tag

`scripts/audit_jd_corpus.py` found two defects in the committed bodies that are
properties of how they were *fetched*, not of the postings themselves:

* **48 postings cut at exactly 6,000 characters**, mid-word, because
  `job_descriptions.MAX_TEXT` was 6,000 — and 14 exceeded it, because the feed
  adapters never applied it, so the corpus carried two different ceilings.
* **18 postings carrying literal `<li>` / `<p>` markup as visible text**,
  because `strip_html` removed tags *before* unescaping entities and every
  double-escaped body therefore emerged as its own source markup.

Both are fixed in `scripts/job_descriptions.py`, and neither fix reaches the
committed files without re-fetching.

**This repairs; it does not re-select.** Re-running
`scripts/scrape_job_descriptions.py` would redo discovery, selection and
verification, and would hand back a materially different 150 postings — a change
to *which* postings the benchmark measures, bundled with a fix to *how their text
was extracted*, with no way to attribute a moved number to either. So this script
touches exactly one field. Ids, `role_family`, `level`, `verified`, ordering and
membership are untouched, and a re-fetch that fails or comes back worse than what
is committed leaves the existing body in place rather than degrading it.

The cache is bypassed deliberately: it stores post-processing text, so every
cached body carries the same damage the fetch code just stopped producing.
Successful re-fetches are written back to it.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import job_descriptions  # noqa: E402
from audit_jd_corpus import DATASET_DIR, audit_task  # noqa: E402

# A re-fetch this much shorter than what is committed is treated as a worse
# body, not a repair: expired postings commonly return a short "no longer
# accepting applications" page that would otherwise overwrite a good body.
MIN_KEEP_RATIO = 0.6


@dataclass
class Repair:
    task_id: str
    action: str            # "repaired" | "unchanged" | "kept-existing" | "failed"
    before: int
    after: int
    detail: str = ""
    # The re-fetched body, carried on the result so `--apply` writes what the
    # dry run evaluated. Fetching a second time to write would double the
    # requests and could write a body the checks above never saw.
    text: str = ""


def _issues(task: Dict, path: Path) -> set:
    return {i.check for i in audit_task(task, path)}


def repair_one(path: Path, only: Optional[set] = None) -> Repair:
    task = json.loads(path.read_text(encoding="utf-8"))
    tid = task.get("id") or path.stem
    old = task.get("description") or ""
    before = _issues(task, path)

    if only is not None and not (before & only):
        return Repair(tid, "unchanged", len(old), len(old), "no targeted issue")

    text, status, detail = job_descriptions.fetch_one(task.get("url") or "")
    if status != "ok" or not text:
        return Repair(tid, "failed", len(old), 0, detail or status)
    if len(text) < len(old) * MIN_KEEP_RATIO:
        return Repair(tid, "kept-existing", len(old), len(text),
                      f"re-fetch is {len(text)/max(len(old), 1):.0%} of the "
                      "committed body — likely an expired posting")

    candidate = dict(task, description=text)
    after = _issues(candidate, path)
    new_errors = {c for c in after - before}
    if new_errors:
        return Repair(tid, "kept-existing", len(old), len(text),
                      f"re-fetch introduces {', '.join(sorted(new_errors))}")
    if text == old:
        return Repair(tid, "unchanged", len(old), len(text), "byte-identical")

    return Repair(tid, "repaired", len(old), len(text),
                  f"cleared {', '.join(sorted(before - after)) or 'no issue'}",
                  text=text)


def repair_corpus(dataset_dir: Path = DATASET_DIR, apply: bool = False,
                  only: Optional[set] = None, workers: int = 6) -> List[Repair]:
    paths = sorted(Path(dataset_dir).glob("*.json"))
    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(lambda p: _repair_and_maybe_write(p, apply, only), paths))
    return results


def _repair_and_maybe_write(path: Path, apply: bool, only: Optional[set]) -> Repair:
    result = repair_one(path, only)
    if apply and result.action == "repaired" and result.text:
        task = json.loads(path.read_text(encoding="utf-8"))
        task["description"] = result.text
        path.write_text(json.dumps(task, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    return result


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="rewrite descriptions (default: report only)")
    ap.add_argument("--only", default=None,
                    help="comma-separated audit checks to target, e.g. truncated,html_tag")
    ap.add_argument("--dir", type=Path, default=DATASET_DIR)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args(argv)

    only = set(args.only.split(",")) if args.only else None
    print(f"{'REPAIRING' if args.apply else 'DRY RUN'} — "
          f"{len(sorted(Path(args.dir).glob('*.json')))} postings, "
          f"targeting {', '.join(sorted(only)) if only else 'every posting'}\n")

    results = repair_corpus(args.dir, apply=args.apply, only=only,
                            workers=args.workers)

    by_action: Dict[str, List[Repair]] = {}
    for result in results:
        by_action.setdefault(result.action, []).append(result)
    for action in ("repaired", "unchanged", "kept-existing", "failed"):
        group = by_action.get(action, [])
        print(f"{action:<16}{len(group)}")
        for result in group[:8] if action in ("kept-existing", "failed") else []:
            print(f"    {result.task_id[:52]:<54}{result.detail}")

    grew = sum(r.after - r.before for r in by_action.get("repaired", []))
    print(f"\n{grew:+,} characters of posting text "
          f"{'written' if args.apply else 'available'}")
    if not args.apply:
        print("\nnothing written — rerun with --apply")
    return 0 if not by_action.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
