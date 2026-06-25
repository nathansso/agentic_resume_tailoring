"""
One-time backfill of cached skill embeddings (issue #54, Phase 2).

Populates Skill.embedding / Skill.embedding_model for every skill that lacks a
vector or was embedded with a different model. Safe to re-run — it only touches
stale rows.

Usage:
    python scripts/backfill_skill_embeddings.py
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from database.db import engine, init_db  # noqa: E402
from agents.skill_embeddings import ensure_skill_embeddings  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    init_db()
    with Session(engine) as session:
        count = ensure_skill_embeddings(session)  # all skills
    print(f"Backfilled embeddings for {count} skill(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
