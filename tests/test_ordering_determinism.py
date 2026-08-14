"""Ordering determinism under a tied `created_at` (issue #180).

The defect these pin: `load_chat_history` and `_prune_chat_messages` ordered
only on `ChatMessage.created_at`, which has no tiebreaker. Messages written in
the same clock tick came back in arbitrary order — a conversation could render
with the assistant's reply above the user's question, and the prune deleted an
arbitrary set of messages rather than the oldest.

**Why these tests force the tie rather than relying on timing.** The bug
reproduces every run on Windows, where `datetime.utcnow()` is coarse enough that
200 consecutive calls return one value, and almost never on Linux, where it
resolves to microseconds — so CI stayed green through the whole life of the
defect. A test that writes quickly and hopes for a collision is a test that only
runs on one developer's machine. These write normally and then collapse
`created_at` to a single value, which is the same state the bug produced and is
reached identically on every platform and both engines.
"""
from datetime import datetime
from uuid import uuid4

from sqlmodel import Session, select

import services as services_module
from database.models import ChatMessage, JobDescription

FIXED = datetime(2026, 8, 12, 12, 0, 0)


def _make_job(engine, title="Ordering Test"):
    with Session(engine) as session:
        job = JobDescription(title=title, company="Co", description="")
        session.add(job)
        session.commit()
        session.refresh(job)
        return str(job.job_id)


def _collapse_created_at(engine, value=FIXED):
    """Put every chat row on one `created_at` — the state the bug produced."""
    with Session(engine) as session:
        for row in session.exec(select(ChatMessage)).all():
            row.created_at = value
            session.add(row)
        session.commit()


def _clear_seq(engine):
    """Return every row to its pre-#180 shape, for the backfill tests."""
    with Session(engine) as session:
        for row in session.exec(select(ChatMessage)).all():
            row.seq = None
            session.add(row)
        session.commit()


def test_insertion_order_survives_a_total_created_at_tie(isolated_engine):
    """Ten messages sharing one timestamp still read back in write order."""
    job_id = _make_job(isolated_engine)
    for i in range(10):
        services_module.save_chat_message(job_id, "user", f"msg {i}")
    _collapse_created_at(isolated_engine)

    history = services_module.load_chat_history(job_id, limit=50)

    assert [m["content"] for m in history] == [f"msg {i}" for i in range(10)]


def test_question_and_reply_do_not_swap(isolated_engine):
    """The user-visible symptom: a reply must not render above its question.

    `agents/chat.py` writes these two back to back, which is exactly the pair
    that landed on one timestamp in production.
    """
    job_id = _make_job(isolated_engine)
    services_module.save_chat_message(job_id, "user", "what is my top skill?")
    services_module.save_chat_message(job_id, "assistant", "Python.")
    _collapse_created_at(isolated_engine)

    history = services_module.load_chat_history(job_id)

    assert [m["role"] for m in history] == ["user", "assistant"]


def test_limit_window_is_stable_when_a_tie_straddles_its_boundary(isolated_engine):
    """`.limit()` cuts the sorted window, so an undefined order dropped rows.

    Not merely a reordering: with every row tied, which two messages fall inside
    `limit=2` was up to the engine, so an arbitrary message went missing from
    the returned window rather than appearing in the wrong place.
    """
    job_id = _make_job(isolated_engine)
    for i in range(4):
        services_module.save_chat_message(job_id, "user", f"msg {i}")
    _collapse_created_at(isolated_engine)

    history = services_module.load_chat_history(job_id, limit=2)

    assert [m["content"] for m in history] == ["msg 2", "msg 3"]


def test_prune_deletes_the_oldest_under_a_total_tie(isolated_engine):
    """The data-loss half: the cap held, but the wrong rows were deleted.

    Written directly rather than through `save_chat_message` so every row is
    tied *before* the prune runs — going through the service would prune as it
    wrote, against timestamps that had not been collapsed yet.
    """
    job_id = _make_job(isolated_engine)
    keep = services_module._MAX_CHAT_MESSAGES_PER_JOB
    from uuid import UUID

    with Session(isolated_engine) as session:
        for i in range(keep + 5):
            session.add(ChatMessage(job_id=UUID(job_id), role="user",
                                    content=f"msg {i}", seq=i, created_at=FIXED))
        session.commit()

    services_module._prune_chat_messages(UUID(job_id))

    history = services_module.load_chat_history(job_id, limit=keep + 10)
    assert len(history) == keep
    assert history[0]["content"] == "msg 5"
    assert history[-1]["content"] == f"msg {keep + 4}"


def test_repeated_reads_are_identical(isolated_engine):
    """Same query, same answer — the property #158 and #171 each paid for."""
    job_id = _make_job(isolated_engine)
    for i in range(20):
        services_module.save_chat_message(job_id, "user", f"msg {i}")
    _collapse_created_at(isolated_engine)

    reads = [
        [m["content"] for m in services_module.load_chat_history(job_id, limit=50)]
        for _ in range(10)
    ]

    assert all(r == reads[0] for r in reads)


def test_identical_messages_are_not_collapsed_or_reordered(isolated_engine):
    """Two byte-identical messages are legitimately identical.

    This is why the fix records insertion order instead of breaking ties on
    content: no content key can separate these two rows, so a content-based
    tiebreaker would leave them in an undefined order.
    """
    job_id = _make_job(isolated_engine)
    for _ in range(3):
        services_module.save_chat_message(job_id, "user", "same text")
    _collapse_created_at(isolated_engine)

    history = services_module.load_chat_history(job_id)

    assert [m["content"] for m in history] == ["same text"] * 3
    with Session(isolated_engine) as session:
        seqs = sorted(r.seq for r in session.exec(select(ChatMessage)).all())
    assert seqs == [0, 1, 2], "each row keeps its own ordinal despite identical text"


# ── Backfill of rows that predate the column ────────────────────────


def test_backfill_recovers_order_from_distinct_timestamps(isolated_engine):
    """Legacy rows with usable timestamps are restored to their true order.

    The realistic production shape: the deployed database runs on Linux, where
    `datetime.utcnow()` resolves to microseconds, so the great majority of
    existing rows carry distinct `created_at` values and their order *is*
    recoverable. This is the case the backfill exists to serve.
    """
    import database.db as db
    from datetime import timedelta

    job_id = _make_job(isolated_engine)
    for i in range(5):
        services_module.save_chat_message(job_id, "user", f"msg {i}")
    with Session(isolated_engine) as session:
        rows = session.exec(select(ChatMessage)).all()
        for row in sorted(rows, key=lambda r: r.seq):
            row.created_at = FIXED + timedelta(seconds=row.seq)
            row.seq = None
            session.add(row)
        session.commit()

    db._backfill_chat_seq()

    history = services_module.load_chat_history(job_id, limit=50)
    assert [m["content"] for m in history] == [f"msg {i}" for i in range(5)]


def test_backfill_of_fully_tied_rows_is_deterministic_not_original(isolated_engine):
    """Rows tied on everything keep loading, in a frozen but arbitrary order.

    The honest limit of the migration, asserted rather than left implicit: when
    legacy rows share a `created_at` *and* a role, nothing distinguishes them,
    so the backfill cannot restore the order they were written in. What it does
    guarantee is that they all survive, receive contiguous ordinals, and read
    back the same way every time afterwards.

    Asserting recovery here instead would be asserting a promise the design
    explicitly declines to make.
    """
    import database.db as db

    job_id = _make_job(isolated_engine)
    for i in range(5):
        services_module.save_chat_message(job_id, "user", f"msg {i}")
    _collapse_created_at(isolated_engine)
    _clear_seq(isolated_engine)

    db._backfill_chat_seq()

    first = [m["content"] for m in services_module.load_chat_history(job_id, limit=50)]
    assert sorted(first) == [f"msg {i}" for i in range(5)], "no row may be lost"
    with Session(isolated_engine) as session:
        seqs = sorted(r.seq for r in session.exec(select(ChatMessage)).all())
    assert seqs == [0, 1, 2, 3, 4], "ordinals must be contiguous"

    for _ in range(5):
        again = [m["content"]
                 for m in services_module.load_chat_history(job_id, limit=50)]
        assert again == first, "the frozen order must not drift between reads"


def test_backfill_puts_a_tied_question_before_its_reply(isolated_engine):
    """The documented heuristic for unrecoverable history.

    Legacy rows tied on `created_at` carry no recoverable insertion order. The
    backfill breaks that tie `user` before `assistant`, because the dominant
    real tie is a question and its reply written in one tick. Asserted so the
    heuristic is a decision on the record rather than an accident.
    """
    import database.db as db

    job_id = _make_job(isolated_engine)
    services_module.save_chat_message(job_id, "assistant", "the reply")
    services_module.save_chat_message(job_id, "user", "the question")
    _collapse_created_at(isolated_engine)
    _clear_seq(isolated_engine)

    db._backfill_chat_seq()

    history = services_module.load_chat_history(job_id)
    assert [m["role"] for m in history] == ["user", "assistant"]


def test_backfill_is_idempotent(isolated_engine):
    """It runs on every `init_db()`, so a second pass must not renumber."""
    import database.db as db

    job_id = _make_job(isolated_engine)
    for i in range(4):
        services_module.save_chat_message(job_id, "user", f"msg {i}")
    _clear_seq(isolated_engine)

    db._backfill_chat_seq()
    with Session(isolated_engine) as session:
        first = {str(r.message_id): r.seq
                 for r in session.exec(select(ChatMessage)).all()}
    db._backfill_chat_seq()
    with Session(isolated_engine) as session:
        second = {str(r.message_id): r.seq
                  for r in session.exec(select(ChatMessage)).all()}

    assert first == second


def test_backfill_continues_after_existing_seq_values(isolated_engine):
    """A partly-assigned conversation must not restart at 0 and collide.

    Reachable in production: the column is added and backfilled at startup, and
    a message written between those two steps has no `seq` while its neighbours
    already do.
    """
    import database.db as db
    from uuid import UUID

    job_id = _make_job(isolated_engine)
    services_module.save_chat_message(job_id, "user", "first")
    services_module.save_chat_message(job_id, "user", "second")
    with Session(isolated_engine) as session:
        stray = ChatMessage(job_id=UUID(job_id), role="user",
                            content="unnumbered", created_at=FIXED)
        session.add(stray)
        session.commit()

    db._backfill_chat_seq()

    with Session(isolated_engine) as session:
        seqs = sorted(r.seq for r in session.exec(select(ChatMessage)).all())
    assert seqs == [0, 1, 2], f"expected contiguous ordinals, got {seqs}"


def test_resume_sections_read_back_in_document_order_not_storage_order(isolated_engine):
    """Document order must come from `seq`, not from how rows happen to sit.

    **Rows are written in reverse.** A test that writes in the intended order
    proves nothing: SQLite returns tied rows in rowid order, which coincides
    with insertion order, so a `created_at`-only query passes it while still
    being undefined. Writing 3,2,1,0 and expecting 0,1,2,3 back forces the read
    to use the ordinal — and it is the honest shape of the risk anyway, since
    ingestion merge paths and re-ingest do not append in document order.

    This group reaches the rendered resume, not just a debug view:
    `get_achievements` documents itself as returning "in resume-document order",
    which ordering on a shared `created_at` could not deliver.
    """
    from conftest import _seed_user_and_skill
    from database.models import Achievement, Education, Experience, Project

    seeded = _seed_user_and_skill(isolated_engine)
    uid = seeded.user_id

    with Session(isolated_engine) as session:
        for i in reversed(range(4)):
            session.add(Experience(user_id=uid, title=f"Role {i}", company=f"Co {i}",
                                   created_at=FIXED, seq=i))
            session.add(Project(user_id=uid, name=f"Project {i}",
                                created_at=FIXED, seq=i))
            session.add(Education(user_id=uid, institution=f"School {i}",
                                  degree=f"Degree {i}", created_at=FIXED, seq=i))
            session.add(Achievement(user_id=uid, title=f"Award {i}",
                                    created_at=FIXED, seq=i))
        session.commit()

    assert [e["title"] for e in services_module.get_experiences(uid)] == \
        [f"Role {i}" for i in range(4)]
    assert [p["name"] for p in services_module.get_projects(uid)] == \
        [f"Project {i}" for i in range(4)]
    assert [e["institution"] for e in services_module.get_education(uid)] == \
        [f"School {i}" for i in range(4)]
    assert [a["title"] for a in services_module.get_achievements(uid)] == \
        [f"Award {i}" for i in range(4)]


def test_next_seq_numbers_a_section_in_write_order(isolated_engine):
    """The write half: consecutive `next_seq` calls return consecutive ordinals.

    Relies on SQLAlchemy autoflushing pending rows before evaluating the MAX,
    which is what makes this work mid-loop with nothing committed yet. Pinned
    because that behaviour is not visible at the call site in `parser.py`.
    """
    from conftest import _seed_user_and_skill
    from database.db import next_seq
    from database.models import Experience

    seeded = _seed_user_and_skill(isolated_engine)
    uid = seeded.user_id

    with Session(isolated_engine) as session:
        for i in range(5):
            session.add(Experience(user_id=uid, title=f"Role {i}", company="Co",
                                   created_at=FIXED,
                                   seq=next_seq(session, Experience, uid)))
        session.commit()

    with Session(isolated_engine) as session:
        rows = session.exec(select(Experience).order_by(Experience.seq)).all()
    assert [r.seq for r in rows] == [0, 1, 2, 3, 4]
    assert [r.title for r in rows] == [f"Role {i}" for i in range(5)]


def test_next_seq_continues_across_separate_ingests(isolated_engine):
    """A later ingest appends after existing rows instead of restarting at 0."""
    from conftest import _seed_user_and_skill
    from database.db import next_seq
    from database.models import Experience

    seeded = _seed_user_and_skill(isolated_engine)
    uid = seeded.user_id

    for batch in range(2):
        with Session(isolated_engine) as session:
            for i in range(3):
                session.add(Experience(user_id=uid, title=f"batch {batch} role {i}",
                                       company="Co", created_at=FIXED,
                                       seq=next_seq(session, Experience, uid)))
            session.commit()

    with Session(isolated_engine) as session:
        seqs = sorted(r.seq for r in session.exec(select(Experience)).all())
    assert seqs == [0, 1, 2, 3, 4, 5]


def test_document_backfill_assigns_contiguous_ordinals(isolated_engine):
    """Résumé rows predating the column are ordered and keep loading.

    No role-style heuristic applies on this side — nothing in a résumé row says
    which of two entries came first in the source document — so rows tied on
    `created_at` get a frozen arbitrary order, exactly as documented.
    """
    import database.db as db
    from datetime import timedelta
    from conftest import _seed_user_and_skill
    from database.models import Experience

    seeded = _seed_user_and_skill(isolated_engine)
    uid = seeded.user_id

    with Session(isolated_engine) as session:
        for i in range(4):
            session.add(Experience(user_id=uid, title=f"Role {i}", company="Co",
                                   created_at=FIXED + timedelta(minutes=i)))
        session.commit()

    db._backfill_document_seq()

    with Session(isolated_engine) as session:
        rows = session.exec(select(Experience).order_by(Experience.seq)).all()
    assert [r.seq for r in rows] == [0, 1, 2, 3]
    assert [e["title"] for e in services_module.get_experiences(uid)] == \
        [f"Role {i}" for i in range(4)]


def test_unassigned_rows_sort_last_on_both_engines(isolated_engine):
    """A row with no ordinal must not land in an engine-dependent position.

    SQLite and Postgres disagree about where NULLs sort, so a read path that let
    NULL reach the comparison would order differently on the two engines — the
    same both-engine divergence #149 exists to catch. The read paths put
    unassigned rows last explicitly rather than inheriting either default.
    """
    from conftest import _seed_user_and_skill
    from database.models import Experience

    seeded = _seed_user_and_skill(isolated_engine)
    uid = seeded.user_id

    # Written unassigned-first, so storage order disagrees with the expected
    # answer and the assertion cannot pass by coincidence.
    with Session(isolated_engine) as session:
        session.add(Experience(user_id=uid, title="unassigned", company="Co",
                               created_at=FIXED, seq=None))
        session.add(Experience(user_id=uid, title="ordered", company="Co",
                               created_at=FIXED, seq=0))
        session.commit()

    titles = [e["title"] for e in services_module.get_experiences(uid)]
    assert titles == ["ordered", "unassigned"]


def test_latest_result_is_decided_by_the_run_that_produced_it(isolated_engine):
    """"The latest result" means the latest *run*, even when timestamps tie.

    `max(results, key=lambda r: r.created_at)` returned the first maximal
    element in iteration order, and that order came from an unordered
    `select()` — so this could resolve differently between runs and engines.
    It decides the score shown for a job and the content exported for it.

    The run ordinal makes the answer recoverable rather than merely stable:
    run 2 wins because it is run 2, not because its uuid happened to sort last.

    **The uuids are pinned, and that is what makes this a real test.** The old
    tiebreaker was `str(result_id)` over a random uuid4, so an unpinned version
    of this test picks the right row roughly one time in three and passes
    against the broken code by luck — it did exactly that when first written.
    Here the uuid order is the *reverse* of the run order, and rows are written
    newest-first, so neither storage order nor uuid order can produce the
    expected answer by accident.
    """
    from conftest import _seed_user_and_skill
    from database.db import latest_result
    from database.models import UserJobResult
    from uuid import UUID

    seeded = _seed_user_and_skill(isolated_engine)
    job_id = _make_job(isolated_engine)

    runs = [
        (0, 10.0, UUID("ffffffff-0000-4000-8000-000000000000")),
        (1, 20.0, UUID("88888888-0000-4000-8000-000000000000")),
        (2, 30.0, UUID("00000000-0000-4000-8000-000000000000")),
    ]
    with Session(isolated_engine) as session:
        for run, score, result_id in reversed(runs):
            session.add(UserJobResult(result_id=result_id,
                                      user_id=seeded.user_id, job_id=UUID(job_id),
                                      ats_score=score, created_at=FIXED, seq=run))
        session.commit()

    with Session(isolated_engine) as session:
        rows = session.exec(select(UserJobResult)).all()
        assert latest_result(rows).ats_score == 30.0


def test_result_run_ordinals_are_per_job(isolated_engine):
    """Each job carries its own run sequence.

    Numbering results globally would make one job's analyze runs advance
    another's ordinals, so "run 2 of this job" would stop meaning anything.
    """
    from conftest import _seed_user_and_skill
    from database.db import next_seq
    from database.models import UserJobResult
    from uuid import UUID

    seeded = _seed_user_and_skill(isolated_engine)
    job_a = _make_job(isolated_engine, title="Job A")
    job_b = _make_job(isolated_engine, title="Job B")

    with Session(isolated_engine) as session:
        for job in (job_a, job_b, job_a, job_b, job_a):
            session.add(UserJobResult(
                user_id=seeded.user_id, job_id=UUID(job), created_at=FIXED,
                seq=next_seq(session, UserJobResult, seeded.user_id,
                             job_id=UUID(job)),
            ))
        session.commit()

    with Session(isolated_engine) as session:
        rows = session.exec(select(UserJobResult)).all()
        a = sorted(r.seq for r in rows if str(r.job_id) == job_a)
        b = sorted(r.seq for r in rows if str(r.job_id) == job_b)
    assert a == [0, 1, 2]
    assert b == [0, 1]


def test_unassigned_results_fall_back_to_the_old_behaviour(isolated_engine):
    """A set with no ordinals must behave exactly as it did before.

    Covers rows built directly by a fixture and the window between the column
    arriving and `_backfill_result_seq` running. An assigned row outranks an
    unassigned one; among unassigned rows the old `(created_at, result_id)`
    ordering applies.
    """
    from conftest import _seed_user_and_skill
    from database.db import latest_result
    from database.models import UserJobResult
    from datetime import timedelta
    from uuid import UUID

    seeded = _seed_user_and_skill(isolated_engine)
    job_id = _make_job(isolated_engine)

    with Session(isolated_engine) as session:
        session.add(UserJobResult(user_id=seeded.user_id, job_id=UUID(job_id),
                                  ats_score=1.0, created_at=FIXED, seq=None))
        session.add(UserJobResult(user_id=seeded.user_id, job_id=UUID(job_id),
                                  ats_score=2.0,
                                  created_at=FIXED + timedelta(minutes=1),
                                  seq=None))
        session.commit()
        rows = session.exec(select(UserJobResult)).all()
        assert latest_result(rows).ats_score == 2.0

        # One assigned row now outranks both unassigned ones, whatever their
        # timestamps: it is the only row whose run position is known.
        session.add(UserJobResult(user_id=seeded.user_id, job_id=UUID(job_id),
                                  ats_score=3.0, created_at=FIXED, seq=0))
        session.commit()
        rows = session.exec(select(UserJobResult)).all()
        assert latest_result(rows).ats_score == 3.0


def test_result_backfill_assigns_run_ordinals_per_job(isolated_engine):
    """Results predating the column are numbered per job, in timestamp order."""
    import database.db as db
    from conftest import _seed_user_and_skill
    from database.models import UserJobResult
    from datetime import timedelta
    from uuid import UUID

    seeded = _seed_user_and_skill(isolated_engine)
    job_a = _make_job(isolated_engine, title="Job A")
    job_b = _make_job(isolated_engine, title="Job B")

    with Session(isolated_engine) as session:
        for i, job in enumerate((job_a, job_b, job_a)):
            session.add(UserJobResult(user_id=seeded.user_id, job_id=UUID(job),
                                      ats_score=float(i),
                                      created_at=FIXED + timedelta(minutes=i)))
        session.commit()

    db._backfill_result_seq()

    with Session(isolated_engine) as session:
        rows = session.exec(select(UserJobResult)).all()
        a = sorted(r.seq for r in rows if str(r.job_id) == job_a)
        b = sorted(r.seq for r in rows if str(r.job_id) == job_b)
    assert a == [0, 1]
    assert b == [0]


def test_latest_result_prefers_a_genuinely_newer_row(isolated_engine):
    """The tiebreaker must not override a real timestamp difference."""
    from conftest import _seed_user_and_skill
    from database.db import latest_result
    from database.models import UserJobResult
    from datetime import timedelta
    from uuid import UUID

    seeded = _seed_user_and_skill(isolated_engine)
    job_id = _make_job(isolated_engine)

    with Session(isolated_engine) as session:
        for i, score in enumerate((10.0, 99.0)):
            session.add(UserJobResult(user_id=seeded.user_id, job_id=UUID(job_id),
                                      ats_score=score,
                                      created_at=FIXED + timedelta(minutes=i)))
        session.commit()

    with Session(isolated_engine) as session:
        rows = session.exec(select(UserJobResult)).all()
        assert latest_result(rows).ats_score == 99.0


def test_latest_result_of_nothing_is_none(isolated_engine):
    """The `if results else None` the helper folded in."""
    from database.db import latest_result

    assert latest_result([]) is None


def test_landing_context_and_job_threads_number_independently(isolated_engine):
    """Each conversation carries its own ordinal series.

    Landing context is one conversation *per user* (issue #73); a job thread is
    keyed by job alone. Numbering them together would make one user's landing
    chat advance another's ordinals.
    """
    from conftest import _seed_user_and_skill

    seeded = _seed_user_and_skill(isolated_engine)
    job_id = _make_job(isolated_engine)

    services_module.save_chat_message(None, "user", "landing one")
    services_module.save_chat_message(job_id, "user", "job one")
    services_module.save_chat_message(None, "user", "landing two")
    services_module.save_chat_message(job_id, "user", "job two")

    landing = services_module.load_chat_history(None, user_id=seeded.user_id)
    job = services_module.load_chat_history(job_id)

    assert [m["content"] for m in landing] == ["landing one", "landing two"]
    assert [m["content"] for m in job] == ["job one", "job two"]
