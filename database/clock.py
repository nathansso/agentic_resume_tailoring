"""The one place ART reads the clock (issue #210).

**Column contract.** Every `datetime` the code holds is timezone-aware UTC.

- `utc_now()` produces it. Nothing stores the naive clock readings (utcnow is
  deprecated in 3.12) or a bare `datetime.now()`.
- sqlmodel >= 0.0.47 maps a `datetime` field to `UTCDateTime`: it rejects a
  naive value on write, and returns an aware UTC value on read, **whatever the
  column physically holds**. So the same models read an old SQLite file (naive
  `YYYY-MM-DD HH:MM:SS.ffffff` strings), a Postgres `timestamp without time
  zone` column (production Supabase's existing schema), and a `timestamptz`
  column (a database created fresh by this version) alike. No migration, no
  ALTER of an existing column.
- `as_utc()` is for the values that do *not* come through a model column: a
  timestamp parsed out of a JSON export or a recording, a hand-built fixture.
  It reads a naive value as UTC, which is what every writer before #210 meant.

Wire format: `.isoformat()` of an aware value ends in `+00:00`. Readers accept
both that and the older offset-less form (`as_utc(datetime.fromisoformat(s))`).
"""

from datetime import datetime, timezone
from typing import Optional


def utc_now() -> datetime:
    """The current instant, timezone-aware UTC."""
    return datetime.now(timezone.utc)


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """*value* as aware UTC. A naive value is read as UTC; None passes through."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_utc(text: Optional[str]) -> Optional[datetime]:
    """An ISO-8601 string as aware UTC, accepting both the offset-less form
    older writers produced and the `+00:00` / `Z` form written now."""
    if not text:
        return None
    return as_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
