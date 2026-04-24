from contextlib import contextmanager
from sqlalchemy.orm import Session


@contextmanager
def transactional_session(db: Session):
    try:
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise
