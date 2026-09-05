"""PostgreSQL-only proof that a real 10-digit Indian mobile fits `retailer_requests`.

The SQLite suite cannot see this. SQLite stores any integer in an INTEGER column, so a
`Column(Integer)` holding 9812345678 passes there and then raises
`NumericValueOutOfRange` on PostgreSQL, whose 4-byte integer stops at 2147483647 — which
is exactly how this shipped broken the first time and was caught only by hitting the real
API. `retailers.mobile_number` is BigInteger for the same reason; this pins that the
request table, which feeds it, agrees.

If the dev PostgreSQL database is unreachable these fail loudly. Set ASCEND_ALLOW_PG_SKIP=1
to allow a skip instead — a silently-skipping test is not evidence.
"""

import os

import pytest
from sqlalchemy import text

from app.tests.pg_utils import pg_engine


def _connect_or_skip():
    engine = pg_engine()
    try:
        conn = engine.connect()
        conn.close()
    except Exception:
        if os.getenv("ASCEND_ALLOW_PG_SKIP") == "1":
            pytest.skip("PostgreSQL unreachable and ASCEND_ALLOW_PG_SKIP=1")
        raise
    return engine


@pytest.fixture(scope="module")
def engine():
    return _connect_or_skip()


def test_mobile_number_is_wide_enough_for_a_real_indian_mobile(engine):
    """Reads the system catalog rather than the ORM: a model changed back to Integer without
    a migration would still pass an ORM-level check against a stale database."""
    with engine.connect() as conn:
        data_type = conn.execute(
            text(
                "select data_type from information_schema.columns "
                "where table_name = 'retailer_requests' and column_name = 'mobile_number'"
            )
        ).scalar()

    assert data_type == "bigint", (
        f"retailer_requests.mobile_number is {data_type}; a 10-digit mobile needs bigint, "
        "matching retailers.mobile_number"
    )


def test_postgres_accepts_a_ten_digit_mobile(engine):
    """The behavioural half: prove the real database takes the value, then roll it back."""
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            employee_id = conn.execute(text("select id from employees limit 1")).scalar()
            if employee_id is None:
                pytest.skip("No employees in the dev database to attribute a request to")
            conn.execute(
                text(
                    "insert into retailer_requests (requested_by_employee_id, status, name, mobile_number) "
                    "values (:employee_id, 'PENDING', 'PG width probe', 9812345678)"
                ),
                {"employee_id": employee_id},
            )
            stored = conn.execute(
                text("select mobile_number from retailer_requests where name = 'PG width probe'")
            ).scalar()
            assert stored == 9812345678
        finally:
            # Always rolled back: this test must leave the dev database exactly as it found it.
            transaction.rollback()
