"""
Merge a profile's duplicate skills that already exist (issue #365).

Two `Skill` rows whose names fold to one identity ("PyTorch" and "pytorch", "Time-Series
Analysis" and "time series analysis") used to be linked side by side. New ingests now join the
existing row; this fixes a profile ingested before that. It repoints the user's own skill
links (every evidence source kept), their jobs' skill links and their dismissed-skill
tombstones onto one skill per fold. No global `Skill` row is deleted or renamed, and no other
user's data is touched. Safe to re-run; a second run changes nothing.

The database is whichever `DATABASE_URL` points at (SQLite when it is unset), so name the
user explicitly rather than relying on the active profile.

Usage:
    python scripts/merge_skill_duplicates.py --user-id <uuid> [--dry-run]
"""
import argparse
import logging
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

import database.db as _db  # noqa: E402
from agents.skill_resolver import merge_user_skill_duplicates  # noqa: E402


def merge(user_id: UUID, dry_run: bool = False) -> int:
    """Merge the user's folded duplicates; return how many duplicate skills were merged away.
    With `dry_run` the changes are rolled back."""
    with Session(_db.engine) as session:
        merged = merge_user_skill_duplicates(session, user_id)
        if dry_run:
            session.rollback()
        else:
            session.commit()
    return merged


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--user-id", required=True, type=UUID, help="the profile's user id")
    parser.add_argument("--dry-run", action="store_true", help="count the duplicates, change nothing")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _db.init_db()
    merged = merge(args.user_id, dry_run=args.dry_run)
    verb = "would merge" if args.dry_run else "merged"
    print(f"{verb} {merged} duplicate skill(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
