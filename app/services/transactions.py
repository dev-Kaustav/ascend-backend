from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session


@contextmanager
def transactional_session(db: Session):
    try:
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise


def reclaim_sequences(db: Session, *tables: str) -> None:
    """
    Hand back the ids a rolled-back transaction consumed.

    Postgres sequences are non-transactional: nextval() is never rolled back, so a
    failed bulk insert of N rows leaves an N-wide gap behind. That is by design (it
    is what lets concurrent writers allocate ids without blocking each other), so
    the only safe place to undo it is a bulk path where we know no other writer is
    mid-insert — i.e. an admin import that just aborted.

    Must be called AFTER the rollback, on a clean transaction. No-ops on backends
    without pg_get_serial_sequence (sqlite in tests) and on tables whose id column
    is not sequence-backed.
    """
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    for table in tables:
        # Table names are module constants, never user input — but the name is
        # interpolated (a table cannot be a bind parameter), so keep it locked down.
        if not table.isidentifier():
            raise ValueError(f"invalid table name: {table!r}")
        db.execute(
            text(
                """
                SELECT setval(
                    seq,
                    COALESCE((SELECT MAX(id) FROM {table}), 0) + 1,
                    false
                )
                FROM pg_get_serial_sequence(:table, 'id') AS seq
                WHERE seq IS NOT NULL
                """.format(table=table)
            ),
            {"table": table},
        )
    db.commit()
