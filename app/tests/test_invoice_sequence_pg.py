"""PostgreSQL-only invoice tests — things the default SQLite test engine cannot express:
the invoice_number_seq sequence, its concurrency guarantee, and the immutability triggers
that guard raw SQL bypassing the ORM.

If the dev PostgreSQL database is unreachable, these tests fail loudly by default. Set
ASCEND_ALLOW_PG_SKIP=1 to allow a skip instead — a silently-skipping test is not evidence,
and INV-04/INV-03 are precisely the requirements most likely to rot behind a skip.
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

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


def _insert_order(conn):
    row = conn.execute(
        text(
            "INSERT INTO orders (from_entity_type, from_entity_id, to_entity_type, to_entity_id) "
            "VALUES ('BRAND', 1, 'RETAILER', 1) RETURNING id"
        )
    ).fetchone()
    return row[0]


def _insert_invoice(conn, order_id, invoice_number):
    row = conn.execute(
        text(
            """
            INSERT INTO invoices (
                invoice_number, invoice_date, status, order_id, invoice_type, supply_type,
                reverse_charge, place_of_supply, is_inter_state, supplier_legal_name, buyer_name,
                taxable_value, discount_amount, cgst_amount, sgst_amount, igst_amount, cess_amount,
                total_tax_amount, grand_total
            ) VALUES (
                :invoice_number, now(), 'ISSUED', :order_id, 'B2C', 'REGULAR',
                false, 'Haryana', false, 'Ascend Foods', 'Test Retailer',
                100.00, 0.00, 9.00, 9.00, 0.00, 0.00, 18.00, 118.00
            ) RETURNING id
            """
        ),
        {"invoice_number": invoice_number, "order_id": order_id},
    ).fetchone()
    return row[0]


def test_invoice_number_seq_exists(engine):
    with engine.connect() as conn:
        count = conn.execute(
            text(
                "select count(*) from information_schema.sequences "
                "where sequence_name='invoice_number_seq'"
            )
        ).scalar()
    assert count == 1


def test_concurrent_nextval_produces_distinct_values(engine):
    n = 16
    barrier = threading.Barrier(n)

    def worker():
        conn = engine.connect()
        try:
            barrier.wait()
            return conn.execute(text("select nextval('invoice_number_seq')")).scalar()
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(worker) for _ in range(n)]
        results = [f.result() for f in futures]

    assert len(results) == n
    assert all(isinstance(v, int) for v in results)
    assert len(set(results)) == n


def test_issued_invoice_row_cannot_be_updated_by_raw_sql(engine):
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            order_id = _insert_order(conn)
            invoice_id = _insert_invoice(conn, order_id, "PG-RAW-UPDATE")
            with pytest.raises(DatabaseError):
                conn.execute(
                    text("UPDATE invoices SET grand_total = 1.00 WHERE id = :id"),
                    {"id": invoice_id},
                )
        finally:
            trans.rollback()


def test_issued_invoice_row_cannot_be_deleted_by_raw_sql(engine):
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            order_id = _insert_order(conn)
            invoice_id = _insert_invoice(conn, order_id, "PG-RAW-DELETE")
            with pytest.raises(DatabaseError):
                conn.execute(text("DELETE FROM invoices WHERE id = :id"), {"id": invoice_id})
        finally:
            trans.rollback()
