from app.models import Brand
from app.services.transactions import transactional_session


def test_transactional_session_keeps_outer_transaction_open(db):
    outer = db.begin()
    try:
        db.add(Brand(name="outer"))
        with transactional_session(db):
            db.add(Brand(name="inner"))

        assert db.in_transaction() is True
        outer.rollback()
    finally:
        if db.in_transaction():
            db.rollback()

    assert db.query(Brand).count() == 0
