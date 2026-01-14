from contextlib import contextmanager
from sqlalchemy.orm import Session


@contextmanager
def transactional_session(db: Session):
    if db.in_transaction():
        with db.begin_nested():
            yield
    else:
        with db.begin():
            yield
