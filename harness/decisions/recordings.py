"""Recordings: the Jev cache as a file, and the `art jev` commands (#193).

    art jev status                        # mode, key present (never the key), rows by point
    art jev export recordings.json        # every cached decision, versioned JSON
    art jev import recordings.json        # merge them into this store

A recording is the cache's rows and nothing else: the question, the answer,
the model version that answered, the usage share, the point and the time,
under the hash that keys them. The state (the bullet or message text) is never
stored. A question can still carry text, though: the variant choice lists the
user's approved bullets as its options, the memory gate lists the names of the
user's skills, roles and projects, and a coverage question holds the posting's
requirement text. So a recording made from a real store is personal data, and
`art jev export` says so on stderr; only recordings made from synthetic
profiles (`eval/*_labels/`) are fit to commit as test fixtures.

Import keeps rows already in the store unless `--overwrite`. A recording whose
`format` or `version` is not understood is refused, not guessed at.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from database.clock import parse_utc, utc_now
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

FORMAT = "art-jev-recordings"
VERSION = 1


class RecordingError(ValueError):
    pass


def _row_dict(row) -> Dict[str, Any]:
    return {"cache_key": row.cache_key, "point": row.point,
            "question_version": row.question_version,
            "requested_model": row.requested_model, "resolved_model": row.resolved_model,
            "question": row.question, "answer": row.answer,
            "input_tokens": row.input_tokens, "output_tokens": row.output_tokens,
            "created_at": row.created_at.isoformat() if row.created_at else None}


def export_recordings() -> Dict[str, Any]:
    from harness.decisions import cache
    return {"format": FORMAT, "version": VERSION,
            "exported_at": utc_now().isoformat(timespec="seconds"),
            "decisions": [_row_dict(r) for r in cache.all_rows()]}


def import_recordings(doc: Any, *, overwrite: bool = False) -> Dict[str, int]:
    """Merge a recording into the cache. Returns `{added, skipped, replaced}`."""
    from database.models import JevDecision
    from harness.decisions import cache

    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise RecordingError(f"not an ART Jev recording (format != {FORMAT!r})")
    if doc.get("version") != VERSION:
        raise RecordingError(f"unsupported recording version {doc.get('version')!r}; "
                             f"this ART reads version {VERSION}")
    entries = doc.get("decisions")
    if not isinstance(entries, list):
        raise RecordingError("recording has no `decisions` list")
    required = ("cache_key", "point", "question_version", "requested_model",
                "resolved_model", "question", "answer")
    rows: List[JevDecision] = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or any(k not in e for k in required):
            raise RecordingError(f"decision {i} is missing one of {required}")
        try:
            created = parse_utc(e.get("created_at"))
        except (ValueError, TypeError):
            raise RecordingError(f"decision {i} has a bad created_at") from None
        rows.append(JevDecision(
            **{k: e[k] for k in required}, input_tokens=e.get("input_tokens"),
            output_tokens=e.get("output_tokens"), created_at=created or utc_now()))
    have = set(cache.get_many(r.cache_key for r in rows))
    todo = rows if overwrite else [r for r in rows if r.cache_key not in have]
    if todo and not cache.put_many(todo):
        raise RecordingError("could not write to the store (is it read-only?)")
    return {"added": len([r for r in todo if r.cache_key not in have]),
            "replaced": len([r for r in todo if r.cache_key in have]),
            "skipped": len(rows) - len(todo)}


def status() -> Dict[str, Any]:
    from harness.decisions import cache, engine
    from harness.decisions.client import KEY_ENV, configured_model, resolve_api_key

    points: Dict[str, Dict[str, Any]] = {}
    for r in cache.all_rows():
        p = points.setdefault(r.point, {"decisions": 0, "question_versions": set(),
                                        "resolved_models": set()})
        p["decisions"] += 1
        p["question_versions"].add(r.question_version)
        p["resolved_models"].add(r.resolved_model)
    return {"mode": engine.mode(), "model": configured_model(),
            "key": "set" if resolve_api_key() else f"not set ({KEY_ENV})",
            "cache": {k: {"decisions": v["decisions"],
                          "question_versions": sorted(v["question_versions"]),
                          "resolved_models": sorted(v["resolved_models"])}
                      for k, v in sorted(points.items())}}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="art jev", description="The Jev decision cache.")
    p.add_argument("--database-url", help="DB URL, or 'dotenv' to use .env's DATABASE_URL")
    p.add_argument("--allow-writes", action="store_true",
                   help="Let import write to a remote database.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Mode, key presence and cached decisions per point.")
    e = sub.add_parser("export", help="Write every cached decision to a JSON file.")
    e.add_argument("path", help="Output file, or - for stdout.")
    i = sub.add_parser("import", help="Merge a recording into the cache.")
    i.add_argument("path")
    i.add_argument("--overwrite", action="store_true",
                   help="Replace rows already in the store.")
    return p


def _emit(doc) -> None:
    sys.stdout.write(json.dumps(doc, sort_keys=True, ensure_ascii=False) + "\n")


EXPORT_NOTICE = ("art jev export: this file holds the questions ART asked, which can name your "
                 "approved bullets, skills, roles and projects and the posting's requirements. "
                 "Treat it as personal data.")


def run(args: argparse.Namespace) -> int:
    """Execute against an already-configured database. Tests call this directly."""
    try:
        if args.cmd == "status":
            _emit(status())
        elif args.cmd == "export":
            doc = export_recordings()
            print(EXPORT_NOTICE, file=sys.stderr)       # stderr: stdout may be the document itself
            if args.path == "-":
                _emit(doc)
            else:
                Path(args.path).write_text(
                    json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
                _emit({"exported": len(doc["decisions"]), "path": args.path})
        else:
            try:
                doc = json.loads(Path(args.path).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RecordingError(f"cannot read {args.path}: {exc}") from None
            _emit(import_recordings(doc, overwrite=args.overwrite))
        return 0
    except RecordingError as exc:
        _emit({"error": {"code": "invalid_recording", "message": str(exc)}})
        return 2


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    from harness.runtime import prepare_local_store, resolve_database_url

    url = resolve_database_url(args.database_url, os.environ, allow_writes=args.allow_writes)
    os.environ["DATABASE_URL"] = url
    prepare_local_store(url)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
