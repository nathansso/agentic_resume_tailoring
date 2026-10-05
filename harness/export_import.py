"""`art export` and `art import`: back up a profile, restore it, migrate it once (#195).

    art export --out backup.zip [--user-id <uuid>] [--include-cache]
    art import backup.zip [--user-id <uuid>] [--merge | --replace --confirm-replace]
    art import --from-supabase --source-url <postgres url> --user-id <uuid>

**The bundle** is one zip (`docs/harness.md` § 12 lists what is in it):

    manifest.json            format, format_version, art_version, exported_at (UTC),
                             user_id, include_cache, counts per table, applications
    tables/<table>.json      {"table": ..., "rows": [{column: value, ...}, ...]}
    applications/<...>       a copy of $ART_DATA_DIR/applications/

Rows are written in primary-key order with every column the model has, so two
exports of one store are identical apart from `exported_at` (the zip entries carry a
fixed timestamp). Primary keys are kept: tree parents, `<key>#b<n>` cites, track
baselines and variant ids stay valid. UUIDs are strings and datetimes ISO strings,
both through `_to_json` and `_coerce`, the only places a value changes shape.

**Safety.** Nothing here writes to a source. A Postgres source is opened in a
read-only transaction and checked (`SHOW transaction_read_only`) before the first
read, and only SELECTs run against it. The source URL is explicit (`--source-url`
or `ART_IMPORT_SOURCE_URL`), never `DATABASE_URL`, and never echoed with its
password. The destination of an import is local SQLite. Import binds a user id that
is never guessed, refuses a fallback profile (`is_fallback_profile`) unless
`--allow-fallback-profile`, and refuses a user that already has data unless
`--merge` or `--replace --confirm-replace`. Credentials never leave a store:
`User.password_hash`, `github_access_token` and `supabase_uid` are not columns of the
bundle, and a string that looks like an API key, token or database password is
replaced by `[REDACTED]` on the way out (counted in the manifest).

**Older schemas.** A source or bundle can lag the models. Reading selects only the
columns that exist; importing drops unknown columns and tables with a warning, gives
missing columns the model's defaults, and drops a row missing a required column or
whose required parent is gone. A source whose ids are TEXT rather than uuid is
matched by their hex digits.

Model-free by rule (`tests/test_harness_boundary.py`). `database` is imported inside
functions so the CLI can pin the database before anything reads `.env`.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone

from database.clock import as_utc
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from uuid import UUID

import sqlalchemy as sa

from harness import ART_VERSION

FORMAT = "art-export"
FORMAT_VERSION = 1

# Never columns of a bundle, whatever else changes.
USER_SECRET_COLUMNS = ("password_hash", "github_access_token", "supabase_uid")

# Tables this issue deliberately leaves out, so a new table has to be decided on.
NOT_EXPORTED = {
    "aiusage": "daily call counters of the hosted web app",
    "institutioncanonical": "global ROR lookup cache, rebuilt on first use",
}

# Tables other rows point at, whose ids may already be in the destination.
PARENTS = ("jobdescription", "project", "experience", "education", "skill")

_MAX_UNCOMPRESSED = 2 * 1024 ** 3
_CHUNK = 400
REDACTED = "[REDACTED]"


class PortError(Exception):
    """A refusal or a bad input. `code` is what the JSON error carries."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ── datetimes and values: the one place they change shape ────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return as_utc(dt).isoformat()


def _parse_dt(text: str) -> datetime:
    # A naive stamp is UTC: what every writer before #210 meant, and what an old
    # database, or an export taken from one, holds. sqlmodel refuses a naive
    # value on write.
    return as_utc(datetime.fromisoformat(text.strip().replace("Z", "+00:00")))


def _to_json(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _iso(value)
    return value


def _coerce(column: sa.Column, value: Any, *, from_db: bool) -> Any:
    """`value` as the Python type the model column holds. `from_db` values come
    from untyped SELECTs (JSON arrives as text on both engines, booleans as 0/1)."""
    if value is None:
        return None
    t = column.type
    if isinstance(t, sa.Uuid):
        return value if isinstance(value, UUID) else UUID(str(value))
    if isinstance(getattr(t, "impl", t), sa.DateTime):  # UTCDateTime wraps a DateTime
        return as_utc(value) if isinstance(value, datetime) else _parse_dt(str(value))
    if isinstance(t, sa.JSON):
        if from_db and isinstance(value, (str, bytes)):
            return json.loads(value)
        return value
    if isinstance(t, sa.Boolean):
        return bool(value)
    if isinstance(t, sa.Integer):
        return int(value)
    if isinstance(t, sa.Float):
        return float(value)
    return value


# ── the registry: every table, in foreign-key order ──────────────────────────

@dataclass(frozen=True)
class Spec:
    model: type
    scope: str  # self | user | user_or_job | parent | skills | cache
    via: Optional[Tuple[str, str]] = None  # parent scope: (column, parent table)
    exclude: Tuple[str, ...] = ()
    # Tables keyed by an autoincrement integer: a merge cannot keep the ids, so it
    # skips a row whose natural key is already there and takes new ids.
    natural: Tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.model.__table__.name

    @property
    def table(self) -> sa.Table:
        return self.model.__table__

    @property
    def pk(self) -> Tuple[str, ...]:
        return tuple(c.name for c in self.table.primary_key.columns)

    def columns(self) -> List[sa.Column]:
        return [c for c in self.table.columns if c.name not in self.exclude]

    @property
    def has_user(self) -> bool:
        return "user_id" in self.table.columns and self.scope != "self"

    def foreign_keys(self) -> List[Tuple[str, str, bool]]:
        """(column, parent table, nullable), leaving out the user."""
        out = []
        for col in self.table.columns:
            for fk in col.foreign_keys:
                parent = fk.column.table.name
                if parent != "user":
                    out.append((col.name, parent, bool(col.nullable)))
        return out


@functools.lru_cache(maxsize=1)
def specs() -> Tuple[Spec, ...]:
    from database import models as m

    return (
        Spec(m.User, "self", exclude=USER_SECRET_COLUMNS),
        Spec(m.Skill, "skills"),
        Spec(m.Experience, "user"),
        Spec(m.Education, "user"),
        Spec(m.Project, "user"),
        Spec(m.Achievement, "user"),
        Spec(m.ProjectBlurb, "parent", via=("project_id", "project")),
        Spec(m.UserSkill, "user"),
        Spec(m.JobDescription, "user"),
        Spec(m.JobSkill, "parent", via=("job_id", "jobdescription")),
        Spec(m.UserJobResult, "user"),
        Spec(m.ChatMessage, "user_or_job"),
        Spec(m.JobCard, "user"),
        Spec(m.JDProfile, "user_or_job"),
        Spec(m.UserPreference, "user"),
        Spec(m.PersonaTrait, "user"),
        Spec(m.Persona, "user"),
        Spec(m.DeletedEntry, "user", natural=("entity_type", "key_a", "key_b")),
        Spec(m.TailorNode, "user"),
        Spec(m.JobHead, "user"),
        Spec(m.TreeEvent, "user", natural=("job_id", "node_id", "kind", "created_at")),
        Spec(m.PlanProgram, "user"),
        Spec(m.JobRule, "user"),
        Spec(m.BulletVariant, "user"),
        Spec(m.TrackBaseline, "user"),
        Spec(m.JobRoleFamily, "user"),
        Spec(m.JevDecision, "cache"),
        Spec(m.BlockLineCache, "cache"),
    )


def spec_by_name() -> Dict[str, Spec]:
    return {s.name: s for s in specs()}


def _pk_key(spec: Spec, row: Dict[str, Any]) -> Tuple[str, ...]:
    return tuple(str(row.get(c)) for c in spec.pk)


def _hex(value: Any) -> str:
    return str(value).replace("-", "").lower()


def _hexcol(name: str):
    """A column's text with dashes dropped, lower-cased: matches a uuid column, a
    TEXT column holding a dashed id, and SQLite's 32-hex form alike."""
    return sa.func.lower(sa.func.replace(sa.cast(sa.column(name), sa.Text), "-", ""))


# ── reading a store: the same code for export and for a Postgres source ──────

@dataclass
class Bundle:
    manifest: Dict[str, Any]
    tables: Dict[str, List[Dict[str, Any]]]
    warnings: List[str] = field(default_factory=list)
    # relative posix path -> reader, for the applications/ copy
    files: Dict[str, Any] = field(default_factory=dict)
    _close: Any = None

    def close(self) -> None:
        if self._close:
            self._close()
            self._close = None


def _chunks(items: Sequence, n: int = _CHUNK):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def read_tables(conn: sa.Connection, user_id: UUID, *, include_cache: bool = False
                ) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str]]:
    """One user's rows from any store, as `{table: [row dict]}` with typed values.

    Only SELECTs. Tables and columns the store lacks are skipped and noted, so an
    older schema reads fine. Rows with no owner are taken only when a row of this
    user points at them (a job a pre-#73 result refers to).
    """
    insp = sa.inspect(conn)
    present = {t.lower() for t in insp.get_table_names()}
    db_cols: Dict[str, Set[str]] = {}
    warnings: List[str] = []
    uid = _hex(user_id)
    out: Dict[str, List[Dict[str, Any]]] = {}

    def columns_of(spec: Spec) -> Optional[List[sa.Column]]:
        if spec.name not in db_cols:
            if spec.name not in present:
                warnings.append(f"table {spec.name} is not in the source; skipped")
                db_cols[spec.name] = set()
            else:
                db_cols[spec.name] = {c["name"] for c in insp.get_columns(spec.name)}
                missing = [c.name for c in spec.columns() if c.name not in db_cols[spec.name]]
                if missing:
                    warnings.append(
                        f"table {spec.name}: source has no column(s) {', '.join(missing)}")
        if spec.name not in present:
            return None
        return [c for c in spec.columns() if c.name in db_cols[spec.name]]

    def fetch(spec: Spec, *conds) -> List[Dict[str, Any]]:
        cols = columns_of(spec)
        if cols is None:
            return []
        stmt = sa.select(*[sa.column(c.name) for c in cols]).select_from(sa.table(spec.name))
        for cond in conds:
            stmt = stmt.where(cond)
        rows = []
        for raw in conn.execute(stmt).mappings():
            row = {}
            for c in cols:
                try:
                    row[c.name] = _coerce(c, raw[c.name], from_db=True)
                except (ValueError, TypeError) as exc:
                    raise PortError("bad_source_value",
                                    f"{spec.name}.{c.name}: cannot read {raw[c.name]!r} ({exc})")
            rows.append(row)
        return rows

    def fetch_in(spec: Spec, column: str, values: Iterable[str], *extra) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for chunk in _chunks(sorted(set(values))):
            rows.extend(fetch(spec, _hexcol(column).in_(chunk), *extra))
        return rows

    def can_scope(spec: Spec, column: str) -> bool:
        columns_of(spec)
        return column in db_cols.get(spec.name, set())

    def add_new(spec: Spec, rows: List[Dict[str, Any]]) -> None:
        seen = {_pk_key(spec, r) for r in out[spec.name]}
        out[spec.name].extend(r for r in rows if _pk_key(spec, r) not in seen)

    by = spec_by_name()
    out["user"] = fetch(by["user"], _hexcol("user_id") == uid)
    if not out["user"]:
        raise PortError("no_such_user", "The store has no profile with that user id "
                        "(or the role used to read it cannot see it).")

    job_spec = by["jobdescription"]
    out["jobdescription"] = (fetch(job_spec, _hexcol("user_id") == uid)
                             if can_scope(job_spec, "user_id") else [])
    job_hexes = {_hex(r["job_id"]) for r in out["jobdescription"]}

    # Tables with an owner column (or a job of this user's).
    for spec in specs():
        if spec.scope not in ("user", "user_or_job") or spec.name == "jobdescription":
            continue
        out[spec.name] = (fetch(spec, _hexcol("user_id") == uid)
                          if can_scope(spec, "user_id") else [])
        if spec.scope == "user_or_job" and can_scope(spec, "job_id"):
            no_owner = sa.column("user_id").is_(None) if can_scope(spec, "user_id") else sa.true()
            add_new(spec, fetch_in(spec, "job_id", job_hexes, no_owner))

    # Jobs with no owner that this user's rows point at (a pre-#73 result's job).
    referenced: Set[str] = set()
    for spec in specs():
        for col, parent, _ in spec.foreign_keys():
            if parent == "jobdescription":
                referenced |= {_hex(r[col]) for r in out.get(spec.name, []) if r.get(col)}
    extra = referenced - job_hexes
    if extra and can_scope(job_spec, "user_id"):
        claimed = fetch_in(job_spec, "job_id", extra, sa.column("user_id").is_(None))
        if claimed:
            warnings.append(f"{len(claimed)} job(s) with no owner are used by this profile's "
                            "results; they are taken as this profile's")
            out["jobdescription"].extend(claimed)

    # Children of an owner's row.
    for spec in specs():
        if spec.scope == "parent":
            col, parent = spec.via
            ids = [_hex(r[by[parent].pk[0]]) for r in out[parent]]
            out[spec.name] = fetch_in(spec, col, ids) if can_scope(spec, col) else []

    skill_ids: Set[str] = set()
    for name in ("userskill", "jobskill"):
        skill_ids |= {_hex(r["skill_id"]) for r in out[name] if r.get("skill_id")}
    out["skill"] = fetch_in(by["skill"], "skill_id", skill_ids) if skill_ids else []

    for spec in specs():
        if spec.scope == "cache":
            out[spec.name] = fetch(spec) if include_cache else []
        out[spec.name].sort(key=lambda r, s=spec: _pk_key(s, r))
    return out, warnings


@functools.lru_cache(maxsize=1)
def _secret_re() -> "re.Pattern[str]":
    return re.compile("|".join([
        r"sk-[A-Za-z0-9_\-]{20,}",
        r"gh[pousr]_[A-Za-z0-9]{30,}",
        r"github_pat_[A-Za-z0-9_]{30,}",
        r"AKIA[0-9A-Z]{16}",
        r"xox[baprs]-[A-Za-z0-9\-]{10,}",
        r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"postgres(?:ql)?(?:\+\w+)?://[^:\s/@]+:[^@\s]+@",
    ]))


def _redact_value(value: Any, hits: List[int]) -> Any:
    if isinstance(value, str):
        new, n = _secret_re().subn(REDACTED, value)
        hits[0] += n
        return new
    if isinstance(value, list):
        return [_redact_value(v, hits) for v in value]
    if isinstance(value, dict):
        return {k: _redact_value(v, hits) for k, v in value.items()}
    return value


def redact(tables: Dict[str, List[Dict[str, Any]]]) -> int:
    """Replace anything that looks like a credential, in place. Returns the count."""
    hits = [0]
    for rows in tables.values():
        for row in rows:
            for col, value in list(row.items()):
                row[col] = _redact_value(value, hits)
    return hits[0]


# ── the bundle file ──────────────────────────────────────────────────────────

_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _safe_relpath(name: str) -> Optional[PurePosixPath]:
    """A path inside applications/, or None for anything that could escape it."""
    if "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None
    p = PurePosixPath(name)
    if any(part in ("", ".", "..") for part in p.parts) or not p.parts:
        return None
    return p


def _app_files(root: Path) -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            out[path.relative_to(root).as_posix()] = path
    return out


def _json_bytes(doc: Any) -> bytes:
    return (json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def write_bundle(path: Path, manifest: Dict[str, Any], tables: Dict[str, List[Dict[str, Any]]],
                 files: Dict[str, Path]) -> None:
    """Write a bundle zip. Entries have a fixed timestamp, so two writes of the same
    store differ only in the manifest's `exported_at`."""
    tmp = path.with_name(path.name + ".part")

    def entry(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
        info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        zf.writestr(info, data)

    try:
        with zipfile.ZipFile(tmp, "w") as zf:
            entry(zf, "manifest.json", _json_bytes(manifest))
            for spec in specs():
                if spec.name in tables:
                    rows = [{k: _to_json(v) for k, v in r.items()} for r in tables[spec.name]]
                    entry(zf, f"tables/{spec.name}.json",
                          _json_bytes({"table": spec.name, "rows": rows}))
            for rel, src in files.items():
                entry(zf, f"applications/{rel}", src.read_bytes())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def read_bundle(path: Path) -> Bundle:
    """Open a bundle zip, or the same layout unzipped in a directory."""
    path = Path(path)
    if not path.exists():
        raise PortError("invalid_bundle", f"{path} does not exist")
    if path.is_dir():
        return _read_dir(path)
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise PortError("invalid_bundle", f"{path} is not a zip bundle: {exc}") from None
    if sum(i.file_size for i in zf.infolist()) > _MAX_UNCOMPRESSED:
        zf.close()
        raise PortError("invalid_bundle", "the bundle expands to more than 2 GiB")
    names = {i.filename for i in zf.infolist() if not i.is_dir()}
    return _assemble(
        names, lambda n: zf.read(n), lambda n: (lambda: zf.read(n)), zf.close)


def _read_dir(root: Path) -> Bundle:
    names = {p.relative_to(root).as_posix() for p in root.rglob("*")
             if p.is_file() and not p.is_symlink()}
    return _assemble(names, lambda n: (root / n).read_bytes(),
                     lambda n: (lambda: (root / n).read_bytes()), None)


def _assemble(names: Set[str], read, reader_for, close) -> Bundle:
    try:
        if "manifest.json" not in names:
            raise PortError("invalid_bundle", "no manifest.json: not an ART export")
        try:
            manifest = json.loads(read("manifest.json"))
        except ValueError as exc:
            raise PortError("invalid_bundle", f"manifest.json is not JSON: {exc}") from None
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise PortError("invalid_bundle", f"not an ART export (format != {FORMAT!r})")
        version = manifest.get("format_version")
        if not isinstance(version, int) or version < 1:
            raise PortError("invalid_bundle", f"bad format_version {version!r}")
        if version > FORMAT_VERSION:
            raise PortError("invalid_bundle", f"this bundle is format {version}, from a newer "
                            f"ART; this ART reads up to {FORMAT_VERSION}")
        bundle = Bundle(manifest=manifest, tables={}, _close=close)
        for name in sorted(names):
            if name.startswith("tables/") and name.endswith(".json"):
                table = name[len("tables/"):-len(".json")]
                try:
                    doc = json.loads(read(name))
                except ValueError as exc:
                    raise PortError("invalid_bundle", f"{name} is not JSON: {exc}") from None
                rows = doc.get("rows") if isinstance(doc, dict) else None
                if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                    raise PortError("invalid_bundle", f"{name} has no list of row objects")
                bundle.tables[table] = rows
            elif name.startswith("applications/"):
                rel = _safe_relpath(name[len("applications/"):])
                if rel is None:
                    bundle.warnings.append(f"skipped an unsafe path in the bundle: {name!r}")
                else:
                    bundle.files[rel.as_posix()] = reader_for(name)
            elif name != "manifest.json":
                bundle.warnings.append(f"ignored an unknown entry: {name}")
        return bundle
    except Exception:
        if close:
            close()
        raise


# ── export ───────────────────────────────────────────────────────────────────

class read_only:
    """A connection that cannot write. Postgres: a read-only transaction, verified
    before anything is read (the pooler may drop session options, a transaction
    cannot be dropped). SQLite: `query_only`. Never commits."""

    def __init__(self, engine: sa.Engine):
        self.engine = engine
        self.conn: Optional[sa.Connection] = None

    def __enter__(self) -> sa.Connection:
        dialect = self.engine.dialect.name
        conn = self.engine.connect()
        try:
            if dialect == "postgresql":
                conn = conn.execution_options(postgresql_readonly=True)
                state = conn.execute(sa.text("SHOW transaction_read_only")).scalar()
                if str(state).lower() != "on":
                    raise PortError("not_read_only", "could not open the source read-only; "
                                    "refusing to read it")
            elif dialect == "sqlite":
                conn.exec_driver_sql("PRAGMA query_only = ON")
        except BaseException:
            conn.close()
            raise
        self.conn = conn
        return conn

    def __exit__(self, *exc) -> None:
        assert self.conn is not None
        conn, dialect = self.conn, self.engine.dialect.name
        try:
            conn.rollback()
            # The connection goes back to a pool: leave it writable for whoever is next.
            if dialect == "sqlite":
                conn.exec_driver_sql("PRAGMA query_only = OFF")
            elif dialect == "postgresql":
                conn.execution_options(postgresql_readonly=False)
            conn.rollback()
        finally:
            conn.close()


def _applications_dir() -> Path:
    from harness.render import applications_dir
    return applications_dir()


def export_bundle(engine: sa.Engine, user_id: UUID, out: Path, *, include_cache: bool = False,
                  force: bool = False, apps_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Write one user's store, and `applications/`, to the zip `out`."""
    out = Path(out)
    if out.exists() and not force:
        raise PortError("exists", f"{out} already exists; pass --force to overwrite it")
    with read_only(engine) as conn:
        tables, warnings = read_tables(conn, user_id, include_cache=include_cache)
    redacted = redact(tables)
    for spec in specs():
        if spec.scope == "cache" and not include_cache:
            del tables[spec.name]
    files = _app_files(apps_dir if apps_dir is not None else _applications_dir())
    counts = {name: len(rows) for name, rows in tables.items()}
    manifest = {
        "format": FORMAT, "format_version": FORMAT_VERSION, "art_version": ART_VERSION,
        "exported_at": _now().strftime("%Y-%m-%dT%H:%M:%SZ"), "user_id": str(user_id),
        "include_cache": bool(include_cache), "counts": counts, "redacted": redacted,
        "applications": {"files": len(files),
                         "bytes": sum(p.stat().st_size for p in files.values())},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    write_bundle(out, manifest, tables, files)
    return {"path": str(out), "user_id": str(user_id), "counts": counts,
            "applications": manifest["applications"], "redacted": redacted,
            "warnings": warnings}


# ── import ───────────────────────────────────────────────────────────────────

def _check_not_fallback(email: Optional[str], allow: bool, what: str) -> None:
    from database.user_utils import is_fallback_profile
    if is_fallback_profile(email) and not allow:
        raise PortError(
            "fallback_profile",
            f"{what} is a local fallback profile ({email!r}), not a real account. Fallback "
            "profiles have held seed data and stale pointers, so import refuses to bind one. "
            "Pass the real account's --user-id, or --allow-fallback-profile if this is "
            "deliberate.")


def _prepare_rows(bundle: Bundle, user_id: UUID, warnings: List[str]
                  ) -> Dict[str, List[Dict[str, Any]]]:
    """The bundle's rows as typed dicts for the current models: unknown tables and
    columns dropped, `user_id` bound to `user_id`, required columns checked."""
    by = spec_by_name()
    rows_by_table: Dict[str, List[Dict[str, Any]]] = {}
    for name in sorted(bundle.tables):
        if name not in by:
            warnings.append(f"ignored table {name}: this ART has no such table")
    for spec in specs():
        raw_rows = bundle.tables.get(spec.name)
        if raw_rows is None:
            continue
        cols = {c.name: c for c in spec.columns()}
        required = {n for n, f in spec.model.model_fields.items()
                    if f.is_required() and n in cols}
        unknown: Set[str] = set()
        rows: List[Dict[str, Any]] = []
        for i, raw in enumerate(raw_rows):
            row: Dict[str, Any] = {}
            for key, value in raw.items():
                if key in cols:
                    try:
                        row[key] = _coerce(cols[key], value, from_db=False)
                    except (ValueError, TypeError):
                        warnings.append(f"table {spec.name} row {i}: bad value for {key}; "
                                        "taken as missing")
                else:
                    unknown.add(key)
            if spec.name == "user":
                row["user_id"] = user_id
            elif "user_id" in cols:
                row["user_id"] = user_id
            missing = sorted(c for c in required if row.get(c) is None)
            if not spec.natural:
                missing = sorted(set(missing) | {c for c in spec.pk if row.get(c) is None})
            if missing:
                warnings.append(f"table {spec.name} row {i}: dropped, missing required "
                                f"{', '.join(missing)}")
                continue
            rows.append(row)
        if unknown:
            warnings.append(f"table {spec.name}: dropped unknown column(s) "
                            f"{', '.join(sorted(unknown))}")
        rows_by_table[spec.name] = rows
    return rows_by_table


def _drop_orphans(rows_by_table: Dict[str, List[Dict[str, Any]]], known: Dict[str, Set[Tuple]],
                  warnings: List[str], dropped: Dict[str, int]) -> None:
    """Foreign keys must resolve, in the bundle or in the destination: Postgres
    enforces them and a dangling id breaks the tree. A required parent that is
    missing drops the row; an optional one is cleared."""
    keys: Dict[str, Set[Tuple]] = {s.name: set(known.get(s.name, set())) for s in specs()}
    for spec in specs():
        rows = rows_by_table.get(spec.name)
        if not rows:
            continue
        kept = []
        for r in rows:
            ok = True
            for col, parent, nullable in spec.foreign_keys():
                value = r.get(col)
                if value is None or (str(value),) in keys[parent]:
                    continue
                if nullable:
                    warnings.append(f"table {spec.name}: {col} pointed at a missing "
                                    f"{parent}; cleared")
                    r[col] = None
                else:
                    ok = False
                    warnings.append(f"table {spec.name}: dropped a row whose {parent} "
                                    "is not in the bundle")
                    break
            if ok:
                kept.append(r)
            else:
                dropped[spec.name] = dropped.get(spec.name, 0) + 1
        rows_by_table[spec.name] = kept
        keys[spec.name] |= {_pk_key(spec, r) for r in kept}


def _user_counts(session, user_id: UUID) -> Dict[str, int]:
    """Rows this user already has in the destination, by table (the User row excluded)."""
    from sqlalchemy import func, select

    by = spec_by_name()
    counts: Dict[str, int] = {}
    job_ids = select(by["jobdescription"].table.c.job_id).where(
        by["jobdescription"].table.c.user_id == user_id)
    for spec in specs():
        t = spec.table
        if spec.scope in ("self", "skills", "cache"):
            continue
        if spec.scope == "parent":
            col, parent = spec.via
            ptab = by[parent].table
            sub = select(ptab.c[by[parent].pk[0]]).where(ptab.c.user_id == user_id)
            cond = t.c[col].in_(sub)
        elif spec.scope == "user_or_job":
            cond = sa.or_(t.c.user_id == user_id,
                          sa.and_(t.c.user_id.is_(None), t.c.job_id.in_(job_ids)))
        else:
            cond = t.c.user_id == user_id
        n = session.execute(select(func.count()).select_from(t).where(cond)).scalar() or 0
        if n:
            counts[spec.name] = int(n)
    return counts


def _delete_user_rows(session, user_id: UUID) -> None:
    from sqlalchemy import delete, select

    by = spec_by_name()
    job_ids = select(by["jobdescription"].table.c.job_id).where(
        by["jobdescription"].table.c.user_id == user_id)
    for spec in reversed(specs()):
        t = spec.table
        if spec.scope in ("self", "skills", "cache"):
            continue
        if spec.scope == "parent":
            col, parent = spec.via
            ptab = by[parent].table
            sub = select(ptab.c[by[parent].pk[0]]).where(ptab.c.user_id == user_id)
            session.execute(delete(t).where(t.c[col].in_(sub)))
        elif spec.scope == "user_or_job":
            session.execute(delete(t).where(sa.or_(
                t.c.user_id == user_id,
                sa.and_(t.c.user_id.is_(None), t.c.job_id.in_(job_ids)))))
        else:
            session.execute(delete(t).where(t.c.user_id == user_id))
    session.flush()


def _existing_owners(session, spec: Spec) -> Dict[Tuple[str, ...], Optional[str]]:
    """Primary keys already in the destination, each with its owner's id (None when
    the table has no owner column or the row has none)."""
    t = spec.table
    cols = [t.c[c] for c in spec.pk]
    owner = t.c.user_id if spec.has_user and "user_id" not in spec.pk else None
    stmt = sa.select(*cols, *([owner] if owner is not None else []))
    out: Dict[Tuple[str, ...], Optional[str]] = {}
    for r in session.execute(stmt).all():
        key = tuple(str(v) for v in r[:len(cols)])
        if "user_id" in spec.pk:  # the key names its owner
            out[key] = key[spec.pk.index("user_id")]
        else:
            out[key] = (str(r[len(cols)]) if owner is not None and r[len(cols)] is not None
                        else None)
    return out


def _fix_sequences(session) -> None:
    if session.get_bind().dialect.name != "postgresql":
        return
    for spec in specs():
        if spec.natural:
            pk = spec.pk[0]
            session.execute(sa.text(
                f"SELECT setval(pg_get_serial_sequence('{spec.name}', '{pk}'), "
                f"COALESCE((SELECT MAX({pk}) FROM {spec.name}), 1))"))


def import_bundle(engine: sa.Engine, bundle: Bundle, *, user_id: Optional[UUID] = None,
                  mode: str = "refuse", confirm_replace: bool = False, dry_run: bool = False,
                  allow_fallback: bool = False, apps_dir: Optional[Path] = None
                  ) -> Dict[str, Any]:
    """Load `bundle` into `engine`'s store for `user_id` (default: the bundle's own).

    `mode`: `refuse` (the default) needs the user to have no data there; `merge` adds
    what is missing and never overwrites; `replace` deletes the user's rows first and
    needs `confirm_replace`. All of it is one transaction; `dry_run` rolls it back.
    """
    from sqlmodel import Session

    from database.models import User

    if mode not in ("refuse", "merge", "replace"):
        raise PortError("invalid_arguments", f"unknown mode {mode!r}")
    manifest_uid = bundle.manifest.get("user_id")
    if user_id is None:
        try:
            user_id = UUID(str(manifest_uid))
        except ValueError:
            raise PortError("user_id_required", "the bundle names no usable user_id; pass "
                            "--user-id") from None
    warnings: List[str] = list(bundle.warnings)
    if str(user_id) != str(manifest_uid):
        warnings.append(f"the bundle's profile {manifest_uid} is imported as {user_id}")
    rows_by_table = _prepare_rows(bundle, user_id, warnings)
    incoming_users = rows_by_table.get("user", [])
    incoming = incoming_users[0] if incoming_users else None
    if incoming is not None:
        _check_not_fallback(incoming.get("email"), allow_fallback, "the profile being imported")

    imported: Dict[str, int] = {}
    skipped: Dict[str, int] = {}
    dropped: Dict[str, int] = {}

    with Session(engine) as session:
        local = session.get(User, user_id)
        if local is not None:
            _check_not_fallback(local.email, allow_fallback, "the destination profile")
        existing = _user_counts(session, user_id) if local is not None else {}
        if existing and mode == "refuse":
            raise PortError("destination_not_empty", (
                f"user {user_id} already has data here ({_fmt_counts(existing)}). "
                "Pass --merge to add what is missing, or --replace --confirm-replace to "
                "delete it first."))
        if mode == "replace" and existing and not confirm_replace:
            raise PortError("confirm_required", (
                f"--replace would delete user {user_id}'s existing data "
                f"({_fmt_counts(existing)}). Pass --confirm-replace to do it."))

        if incoming is not None:
            other = session.execute(sa.select(User.user_id).where(
                User.email == incoming["email"], User.user_id != user_id)).first()
            if other is not None:
                raise PortError("email_conflict", "another profile here already uses "
                                f"{incoming['email']!r}; import under that profile's --user-id "
                                "or change one email first")
            if incoming.get("username"):
                clash = session.execute(sa.select(User.user_id).where(
                    User.username == incoming["username"], User.user_id != user_id)).first()
                if clash is not None:
                    warnings.append("username is taken by another profile here; left unset")
                    incoming["username"] = None

        if mode == "replace":
            _delete_user_rows(session, user_id)

        # Rows already in the destination, per table: kept in a merge, a conflict otherwise.
        owners: Dict[str, Dict[Tuple[str, ...], Optional[str]]] = {}
        for spec in specs():
            if spec.name != "user" and (rows_by_table.get(spec.name) or spec.name in PARENTS):
                owners[spec.name] = _existing_owners(session, spec)

        # Skills are shared across users, so a name already there wins over a new id.
        remap = _skill_remap(session, rows_by_table.get("skill", []))
        if remap:
            for r in rows_by_table.get("userskill", []) + rows_by_table.get("jobskill", []):
                r["skill_id"] = remap.get(r["skill_id"], r["skill_id"])
            rows_by_table["skill"] = [r for r in rows_by_table["skill"]
                                      if r["skill_id"] not in remap]

        # A row may point at a parent already in the destination: always a shared skill,
        # and in a merge the profile's own jobs, roles and projects.
        known = {n: set(owners[n]) for n in PARENTS if n in owners and
                 (mode == "merge" or n == "skill")}
        _drop_orphans(rows_by_table, known, warnings, dropped)

        conflicts: List[str] = []
        for spec in specs():
            for r in rows_by_table.get(spec.name, []):
                if spec.name == "user" or spec.natural:
                    continue
                owner = owners.get(spec.name, {}).get(_pk_key(spec, r), False)
                if owner is False:
                    continue
                if not spec.has_user or spec.scope == "cache":
                    continue  # shared or cache rows: the existing one stays
                if mode == "merge" and owner == str(user_id):
                    continue
                conflicts.append(f"{spec.name} {_pk_key(spec, r)[0]}")
        if conflicts:
            raise PortError("pk_conflict", (
                f"{len(conflicts)} row(s) have the same id as rows of another profile here "
                f"(first: {conflicts[0]}); this bundle was probably already imported under a "
                "different --user-id"))

        # The user row.
        if incoming is not None:
            if local is None:
                session.add(User(**incoming))
            elif mode != "merge":
                for k, v in incoming.items():
                    if k != "user_id":
                        setattr(local, k, v)
                session.add(local)
            imported["user"] = 1 if (local is None or mode != "merge") else 0
        elif local is None:
            stub = {"user_id": user_id, "name": "Imported profile",
                    "email": f"{user_id}@import.invalid"}
            session.add(User(**stub))
            warnings.append("the bundle has no profile row; created a placeholder one")
            imported["user"] = 1
        session.flush()

        for spec in specs():
            if spec.name == "user":
                continue
            rows = rows_by_table.get(spec.name, [])
            if not rows:
                continue
            have = owners.get(spec.name, {})
            natural_have: Set[Tuple] = set()
            if mode == "merge" and spec.natural:
                natural_have = _natural_keys(session, spec, user_id)
            objs = []
            for r in rows:
                taken = _pk_key(spec, r) in have
                if spec.natural:
                    if mode == "merge" and tuple(str(r.get(c)) for c in spec.natural) \
                            in natural_have:
                        skipped[spec.name] = skipped.get(spec.name, 0) + 1
                        continue
                    if taken:  # an autoincrement id someone else holds: take a new one
                        r = {k: v for k, v in r.items() if k not in spec.pk}
                elif taken:
                    skipped[spec.name] = skipped.get(spec.name, 0) + 1
                    continue
                objs.append(spec.model(**r))
            session.add_all(objs)
            session.flush()
            imported[spec.name] = len(objs)
        _fix_sequences(session)
        if dry_run:
            session.rollback()
        else:
            session.commit()

    files = {"written": 0, "unchanged": 0, "skipped": 0}
    if bundle.files:
        files = _restore_files(bundle, apps_dir if apps_dir is not None else _applications_dir(),
                               overwrite=(mode == "replace"), dry_run=dry_run)
    return {"user_id": str(user_id), "mode": mode, "dry_run": dry_run,
            "imported": {k: v for k, v in imported.items() if v},
            "skipped_existing": skipped, "dropped": dropped, "files": files,
            "warnings": warnings[:50] + ([f"... and {len(warnings) - 50} more"]
                                         if len(warnings) > 50 else [])}


def _fmt_counts(counts: Dict[str, int]) -> str:
    return ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))


def _skill_remap(session, skills: List[Dict[str, Any]]) -> Dict[UUID, UUID]:
    """Incoming skill id -> the id of a skill already here with the same name."""
    from database.models import Skill

    if not skills:
        return {}
    have_ids: Set[UUID] = set()
    by_name: Dict[str, UUID] = {}
    for sid, name in session.execute(sa.select(Skill.skill_id, Skill.name)).all():
        have_ids.add(sid)
        by_name.setdefault((name or "").strip().lower(), sid)
    out: Dict[UUID, UUID] = {}
    for r in skills:
        if r["skill_id"] in have_ids:
            continue
        target = by_name.get((r.get("name") or "").strip().lower())
        if target is not None:
            out[r["skill_id"]] = target
    return out


def _natural_keys(session, spec: Spec, user_id: UUID) -> Set[Tuple]:
    t = spec.table
    stmt = sa.select(*[t.c[c] for c in spec.natural]).where(t.c.user_id == user_id)
    return {tuple(str(v) for v in r) for r in session.execute(stmt).all()}


def _restore_files(bundle: Bundle, root: Path, *, overwrite: bool, dry_run: bool) -> Dict[str, int]:
    """Copy applications/ back. A file already there is kept unless `overwrite`."""
    written = unchanged = skipped = 0
    root = root.resolve()
    for rel, read in sorted(bundle.files.items()):
        dest = (root / rel).resolve()
        if root not in dest.parents:
            skipped += 1
            continue
        data = read()
        if dest.exists():
            if dest.is_file() and dest.read_bytes() == data:
                unchanged += 1
                continue
            if not overwrite:
                skipped += 1
                continue
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        written += 1
    return {"written": written, "unchanged": unchanged, "skipped": skipped}


# ── a Postgres source (the one-time web-app migration) ───────────────────────

def mask_url(url: str) -> str:
    try:
        return sa.engine.make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "<unparseable url>"


def open_source(url: str) -> sa.Engine:
    """An engine for the migration source. Postgres only; never writes."""
    u = url.strip()
    if u.startswith("postgres://"):
        u = "postgresql://" + u[len("postgres://"):]
    if not u.startswith(("postgresql://", "postgresql+")):
        raise PortError("invalid_source", "--source-url must be a Postgres URL "
                        "(postgresql://...); the source is a hosted web-app database")
    args = {"connect_args": {"connect_timeout": 15}} if u.startswith("postgresql://") else {}
    try:
        return sa.create_engine(u, **args)
    except ImportError:
        raise PortError("driver_missing", "reading Postgres needs a driver: install the "
                        "[postgres] extra (psycopg2-binary)") from None
    except Exception as exc:
        raise PortError("invalid_source", _scrub(f"cannot open the source: {exc}", url)) from None


def _scrub(message: str, url: str) -> str:
    try:
        pw = sa.engine.make_url(url).password
    except Exception:
        pw = None
    for secret in (pw, url):
        if secret:
            message = message.replace(secret, "***")
    return message.splitlines()[0][:300]


def read_source(engine: sa.Engine, user_id: UUID) -> Bundle:
    """One user's rows from a web-app database, as a bundle with no files."""
    try:
        with read_only(engine) as conn:
            tables, warnings = read_tables(conn, user_id)
    except PortError:
        raise
    except sa.exc.SQLAlchemyError as exc:
        raise PortError("source_unreachable", _scrub(
            f"cannot read the source: {type(exc).__name__}: {exc}",
            engine.url.render_as_string(hide_password=False))) from None
    counts = {k: len(v) for k, v in tables.items() if v}
    if not any(n for k, n in counts.items() if k != "user"):
        raise PortError("no_user_data", "the source has that profile but no data for it; "
                        "check the role can read it (row-level security hides rows from "
                        "restricted roles)")
    redacted = redact(tables)
    manifest = {"format": FORMAT, "format_version": FORMAT_VERSION, "art_version": ART_VERSION,
                "exported_at": _now().strftime("%Y-%m-%dT%H:%M:%SZ"), "user_id": str(user_id),
                "include_cache": False, "counts": counts, "redacted": redacted,
                "source": "supabase"}
    return Bundle(manifest=manifest, tables=_jsonable(tables), warnings=warnings)


def _jsonable(tables: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    """The same shape a bundle file has, so one prepare path serves both."""
    return {n: [{k: _to_json(v) for k, v in r.items()} for r in rows]
            for n, rows in tables.items()}


# ── the commands ─────────────────────────────────────────────────────────────

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="art", description="Back up and migrate an ART profile.")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("export", help="Write the profile and applications/ to a zip bundle.")
    e.add_argument("--out", help="Output .zip, or a directory (default: "
                   "$ART_DATA_DIR/backups/art-export-<time>.zip).")
    e.add_argument("--user-id", help="Profile to export (default: the active profile).")
    e.add_argument("--include-cache", action="store_true",
                   help="Also export the Jev decision cache and block render cache.")
    e.add_argument("--force", action="store_true", help="Overwrite an existing --out file.")
    e.add_argument("--database-url", help="Export from this DB instead of local SQLite.")

    i = sub.add_parser("import", help="Load a bundle, or migrate a web-app profile, "
                       "into the local store.")
    i.add_argument("path", nargs="?", help="A bundle .zip (or the unzipped directory).")
    i.add_argument("--from-supabase", action="store_true",
                   help="Read the profile from a web-app Postgres database instead of a bundle "
                        "(needs --source-url and --user-id).")
    i.add_argument("--source-url", help="The web-app database, postgresql://... Read-only; "
                   "never inferred from DATABASE_URL (or set ART_IMPORT_SOURCE_URL).")
    i.add_argument("--user-id", help="The profile id to bind (required for --from-supabase; "
                   "default for a bundle: the bundle's own).")
    g = i.add_mutually_exclusive_group()
    g.add_argument("--merge", action="store_true",
                   help="Add what is missing; never overwrite what is there.")
    g.add_argument("--replace", action="store_true",
                   help="Delete the profile's existing data, then import (needs "
                        "--confirm-replace).")
    i.add_argument("--confirm-replace", action="store_true",
                   help="Confirm that --replace may delete existing data.")
    i.add_argument("--dry-run", action="store_true",
                   help="Do everything, report, and roll it back.")
    i.add_argument("--allow-fallback-profile", action="store_true",
                   help="Allow binding a local fallback profile (refused by default).")
    i.add_argument("--set-active", action="store_true",
                   help="Make the imported profile the active one for the CLI.")
    return p


def _emit(doc: Any) -> None:
    sys.stdout.write(json.dumps(doc, sort_keys=True, ensure_ascii=False) + "\n")


def _uuid(value: str, flag: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise PortError("invalid_arguments", f"{flag} must be a uuid, not {value!r}") from None


def _export_path(out: Optional[str], user_id: UUID) -> Path:
    from config import APP_DATA_DIR
    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    name = f"art-export-{str(user_id)[:8]}-{stamp}.zip"
    if not out:
        return Path(APP_DATA_DIR) / "backups" / name
    p = Path(out)
    return p / name if (p.is_dir() or out.endswith(("/", "\\"))) else p


def run(args: argparse.Namespace, engine: Optional[sa.Engine] = None) -> int:
    """Execute against an already-configured database. Tests call this directly."""
    try:
        if engine is None:
            import database.db as db
            engine = db.engine
        if args.cmd == "export":
            from database.user_utils import get_active_profile, is_fallback_profile
            if args.user_id:
                uid = _uuid(args.user_id, "--user-id")
                note = None
            else:
                active = get_active_profile()
                if active is None:
                    raise PortError("no_user", "no active profile; pass --user-id")
                uid = active.user_id
                note = ("the active profile looks like a local fallback; pass --user-id if "
                        "your real data is under another profile"
                        if is_fallback_profile(active.email) else None)
            report = export_bundle(engine, uid, _export_path(args.out, uid),
                                   include_cache=args.include_cache, force=args.force)
            if note:
                report["warnings"].append(note)
            _emit(report)
            return 0

        mode = "merge" if args.merge else "replace" if args.replace else "refuse"
        uid = _uuid(args.user_id, "--user-id") if args.user_id else None
        if args.from_supabase:
            if args.path:
                raise PortError("invalid_arguments", "--from-supabase reads a database; "
                                "do not also pass a bundle path")
            if uid is None:
                raise PortError("user_id_required", "--from-supabase needs --user-id: the "
                                "web-app profile to migrate. It is never guessed.")
            url = args.source_url or os.environ.get("ART_IMPORT_SOURCE_URL")
            if not url:
                raise PortError("source_required", "--from-supabase needs --source-url (or "
                                "ART_IMPORT_SOURCE_URL). It is never taken from DATABASE_URL.")
            source = open_source(url)
            try:
                bundle = read_source(source, uid)
            finally:
                source.dispose()
        else:
            if args.source_url:
                raise PortError("invalid_arguments", "--source-url is only for --from-supabase")
            if not args.path:
                raise PortError("invalid_arguments", "give a bundle path, or --from-supabase")
            bundle = read_bundle(Path(args.path))
        try:
            report = import_bundle(engine, bundle, user_id=uid, mode=mode,
                                   confirm_replace=args.confirm_replace, dry_run=args.dry_run,
                                   allow_fallback=args.allow_fallback_profile)
        finally:
            bundle.close()
        report["source"] = "supabase" if args.from_supabase else "bundle"
        if not args.dry_run:
            report["active_profile"] = _bind_active(report["user_id"], args.set_active)
        _emit(report)
        return 0
    except PortError as exc:
        _emit({"error": {"code": exc.code, "message": exc.message}})
        return 2


def _bind_active(user_id: str, set_active: bool) -> Dict[str, Any]:
    """Point the CLI at the imported profile when asked; otherwise say what it points at."""
    import database.user_utils as uu

    current = None
    try:
        current = uu.ACTIVE_PROFILE_FILE.read_text().strip() if uu.ACTIVE_PROFILE_FILE.exists() \
            else None
    except OSError:
        pass
    if set_active:
        uu.ACTIVE_PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
        uu.ACTIVE_PROFILE_FILE.write_text(user_id)
        return {"set": True, "user_id": user_id}
    return {"set": False, "active_user_id": current,
            "matches": current == user_id,
            "hint": None if current == user_id else
            "the CLI's active profile is not the imported one; pass --user-id to tools, "
            "or re-run with --set-active"}


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    from harness.runtime import local_sqlite_path, prepare_local_store, resolve_database_url

    try:
        # Never `DATABASE_URL`: only a flag or ART_MCP_DATABASE_URL moves ART off local SQLite.
        url = resolve_database_url(getattr(args, "database_url", None), os.environ)
        path = local_sqlite_path(url)
        if args.cmd == "import" and path is None:
            raise PortError("remote_destination", "import writes to the local SQLite store "
                            f"only, not {mask_url(url)}")
        if args.cmd == "export" and path is not None and not path.is_file():
            raise PortError("no_store", f"there is no ART store at {path}")
        os.environ["DATABASE_URL"] = url
        if args.cmd == "import":
            prepare_local_store(url)
    except PortError as exc:
        _emit({"error": {"code": exc.code, "message": exc.message}})
        return 2
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
