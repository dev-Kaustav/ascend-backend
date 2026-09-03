"""PostgreSQL-only: a rolled-back import must hand back the ids it consumed.

Sequences are non-transactional in Postgres — nextval() survives a ROLLBACK — so an
aborted bulk import leaves a gap as wide as the batch. reclaim_sequences() closes that
gap on the import paths, where no concurrent writer can be mid-insert. SQLite has no
sequences at all, so this can only be exercised against real PostgreSQL.

Set ASCEND_ALLOW_PG_SKIP=1 to allow a skip when the dev database is unreachable.
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.transactions import reclaim_sequences
from app.tests.pg_utils import pg_engine


def _connect_or_skip():
    engine = pg_engine()
    try:
        engine.connect().close()
    except Exception:
        if os.getenv("ASCEND_ALLOW_PG_SKIP") == "1":
            pytest.skip("PostgreSQL unreachable and ASCEND_ALLOW_PG_SKIP=1")
        raise
    return engine


@pytest.fixture
def db():
    engine = _connect_or_skip()
    session = Session(engine)
    yield session
    session.rollback()
    session.close()


def _next_id(db: Session) -> int:
    """Peek at what the next retailers.id would be, without consuming it."""
    return db.execute(
        text("SELECT last_value, is_called FROM pg_get_serial_sequence('retailers','id')")
    ).fetchone()[0]


def _insert_retailer(db: Session, name: str) -> int:
    return db.execute(
        text("INSERT INTO retailers (name, state) VALUES (:n, 'Haryana') RETURNING id"),
        {"n": name},
    ).fetchone()[0]


def test_rollback_alone_leaves_the_gap(db):
    """Baseline: this is the Postgres behaviour reclaim_sequences exists to correct."""
    before = _insert_retailer(db, "seq-reclaim baseline")
    db.rollback()

    after = _insert_retailer(db, "seq-reclaim baseline 2")
    db.rollback()

    assert after > before, "sequence should have advanced despite the rollback"


def test_reclaim_returns_unconsumed_ids(db):
    """A failed batch's ids are handed back: the next id is max(id)+1, gap closed."""
    high_water = db.execute(text("SELECT COALESCE(MAX(id), 0) FROM retailers")).scalar()

    # Simulate the aborted import: burn a batch of ids, then roll back.
    for i in range(5):
        _insert_retailer(db, f"seq-reclaim burn {i}")
    db.rollback()

    reclaim_sequences(db, "retailers")

    retry = _insert_retailer(db, "seq-reclaim retry")
    db.rollback()
    assert retry == high_water + 1, f"expected reclaimed id {high_water + 1}, got {retry}"


def test_reclaim_never_collides_with_committed_rows(db):
    """Reclaim targets max(id)+1, so it cannot hand out an id that is already taken."""
    kept = _insert_retailer(db, "seq-reclaim committed")
    db.commit()
    try:
        reclaim_sequences(db, "retailers")

        nxt = _insert_retailer(db, "seq-reclaim after committed")
        assert nxt > kept, "reclaim must not reuse a live id"
        db.rollback()
    finally:
        db.execute(text("DELETE FROM retailers WHERE id = :i"), {"i": kept})
        db.commit()
        reclaim_sequences(db, "retailers")


def test_reclaim_rejects_non_identifier_table(db):
    with pytest.raises(ValueError):
        reclaim_sequences(db, "retailers; DROP TABLE retailers")
