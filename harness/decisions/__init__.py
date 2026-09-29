"""Jev decisions (issue #193): bounded judgment calls, cached and replayable.

This package is the only model call under `harness/`, and it is not a
generative one: TypeSafe's Jev answers typed questions (yes-probability, a
choice among enumerated options, a score) about a narrow state and cannot
write text. Everything here is `requests` and the standard library; the import
boundary test (`tests/test_harness_boundary.py`) keeps it that way.

- `questions`: `Noul`, `Choice`, `Score`, canonical JSON, the normalized `Answer`.
- `client`: `JevClient`, the HTTP call with an injectable transport.
- `cache`: the `JevDecision` table, one row per (state, question, model).
- `engine`: `decide(point, state, questions, fallback)` and its three modes.
- `recordings`: export and import of cache rows, and the `art jev` commands.
- `support`: the first decision point, the cited-bullet support check.

Nothing is imported here eagerly, so `harness.acceptance` can read the support
thresholds without loading the client or the database.
"""
