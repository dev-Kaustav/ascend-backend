from app.models import Brand
from app.services.transactions import transactional_session


def test_transactional_session_commits_active_transaction(db):
    db.add(Brand(name="outer"))
    db.flush()

    with transactional_session(db):
        db.add(Brand(name="inner"))

    db.rollback()

    assert db.query(Brand).count() == 2


def test_transactional_session_rolls_back_on_error(db):
    try:
        with transactional_session(db):
            db.add(Brand(name="inner"))
            raise ValueError("boom")
    except ValueError:
        pass

    assert db.query(Brand).count() == 0
