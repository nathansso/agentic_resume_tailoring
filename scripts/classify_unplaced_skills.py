"""
Backfill skill areas for a profile that already exists (issue #318).

Skills are placed in an area by a keyword table; the ones it cannot place are asked of
Jev (TypeSafe) once, when they are ingested. A profile ingested before #318, or with no
`TYPESAFE_API_KEY` set, still has those skills in "Other". This classifies them: one batched
Jev request for the skills with no cached answer, nothing for the rest. Safe to re-run; a
second run makes no call.

The database is whichever `DATABASE_URL` points at (SQLite when it is unset), so name the
user explicitly rather than relying on the active profile.

Usage:
    python scripts/classify_unplaced_skills.py --user-id <uuid> [--dry-run]
"""
import argparse
import logging
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.db import init_db  # noqa: E402
from knowledge_graph.areas import classify_unplaced_skills  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--user-id", required=True, type=UUID, help="the profile's user id")
    parser.add_argument("--dry-run", action="store_true", help="count the unplaced skills, ask nothing")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    init_db()
    stats = classify_unplaced_skills(args.user_id, dry_run=args.dry_run)
    print(f"{stats['skills']} skill(s), {stats['unplaced']} the keyword table cannot place.")
    if not args.dry_run and stats["unplaced"]:
        print(f"Placed {stats['classified']} in an area ({stats['asked']} newly asked of Jev, "
              f"{stats['fallback']} unanswered: no TYPESAFE_API_KEY, ART_JEV_MODE=off or an API error).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
