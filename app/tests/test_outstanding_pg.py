"""PostgreSQL-only coverage for the datetime behavior SQLite cannot prove.

The default suite runs on SQLite, which returns naive values even for
`DateTime(timezone=True)`. Real PostgreSQL returns timezone-aware values. RPT-06 was the
production-only crash caused by subtracting those aware `created_at` values from
`datetime.utcnow()`. Phase 5 coordinator ruling R5 fixes that one defect in
`outstanding.py`; this module proves the real PostgreSQL path now executes and leaves the
dev database unchanged.

If PostgreSQL is unreachable, these tests fail loudly by default. Set
ASCEND_ALLOW_PG_SKIP=1 to allow a skip instead.
"""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
import app.models  # noqa: F401
from app.models import Beat, Brand, Order, OrderItem, OrderItemTax, SKU
from app.models.enums import OrderStatus, PaymentStatus
from app.services.outstanding import get_outstanding_orders, get_outstanding_summary
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


def _seed_qualifying_order(session):
    retailer_id = session.execute(text("select id from retailers limit 1")).scalar()
    warehouse_id = session.execute(text("select id from warehouses limit 1")).scalar()
    assert retailer_id is not None, "seed data expected: at least one retailer"
    assert warehouse_id is not None, "seed data expected: at least one warehouse"

    brand = Brand(name="Outstanding PG Test Brand")
    session.add(brand)
    session.flush()
    sku = SKU(name="Outstanding PG Test SKU", brand_id=brand.id)
    session.add(sku)
    session.flush()
    order = Order(
        from_entity_type="WAREHOUSE",
        from_entity_id=warehouse_id,
        to_entity_type="RETAILER",
        to_entity_id=retailer_id,
        status=OrderStatus.DELIVERED,
        payment_status=PaymentStatus.CREDIT,
    )
    session.add(order)
    session.flush()
    item = OrderItem(order_id=order.id, sku_id=sku.id, quantity=1, unit_price=100, discount_amount=0)
    session.add(item)
    session.flush()
    session.add(OrderItemTax(order_item_id=item.id, tax_type="GST", rate=0))
    session.flush()
    session.refresh(order)
    return order


def test_postgres_returns_aware_timestamps_while_sqlite_harness_returns_naive(engine):
    """Documents the database gap RPT-06 depends on: SQLite cannot prove production
    datetime arithmetic because it drops timezone awareness for the same mapped column
    shape."""
    with engine.connect() as conn:
        Session = sessionmaker(bind=conn)
        session = Session()
        beat = session.query(Beat).filter(Beat.created_at.isnot(None)).first()
        assert beat is not None, "seed data expected: at least one beat with created_at"
        assert beat.created_at.tzinfo is not None
        session.close()

    sqlite_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=sqlite_engine)
    SQLiteSession = sessionmaker(bind=sqlite_engine)
    sqlite_session = SQLiteSession()
    try:
        sqlite_beat = Beat(name="SQLite Timestamp Probe")
        sqlite_session.add(sqlite_beat)
        sqlite_session.commit()
        sqlite_session.refresh(sqlite_beat)
        assert sqlite_beat.created_at.tzinfo is None
    finally:
        sqlite_session.close()
        sqlite_engine.dispose()


def test_outstanding_orders_runs_under_real_postgres_after_rpt_06_fix(engine):
    """RPT-06 fixed: one qualifying PostgreSQL order with an aware `created_at` reaches
    the `days_old` calculation and returns a result instead of raising the old
    offset-naive/offset-aware TypeError."""
    conn = engine.connect()
    trans = conn.begin()
    Session = sessionmaker(bind=conn)
    session = Session()
    try:
        order = _seed_qualifying_order(session)
        assert order.created_at.tzinfo is not None

        items, total = get_outstanding_orders(session)

        assert total == 1
        assert len(items) == 1
        assert items[0]["order_id"] == order.id
        assert isinstance(items[0]["days_old"], int)
    finally:
        session.close()
        trans.rollback()
        conn.close()


def test_outstanding_summary_runs_under_real_postgres_after_rpt_06_fix(engine):
    """RPT-06 fixed for the second public function too: the aging calculation can consume
    PostgreSQL's aware `created_at` and put the bill in the current bucket."""
    conn = engine.connect()
    trans = conn.begin()
    Session = sessionmaker(bind=conn)
    session = Session()
    try:
        order = _seed_qualifying_order(session)
        assert order.created_at.tzinfo is not None

        summary = get_outstanding_summary(session)

        assert summary["total_outstanding"] == 100
        assert summary["aging"]["0_7"] == 100
        assert summary["by_retailer"][0]["bills"] == 1
    finally:
        session.close()
        trans.rollback()
        conn.close()


def test_postgres_outstanding_tests_leave_dev_database_unchanged(engine):
    with engine.connect() as conn:
        counts = {
            table: conn.execute(text(f"select count(*) from {table}")).scalar()
            for table in ("retailers", "orders", "order_items", "accounts", "credit_notes")
        }
    assert counts == {
        "retailers": 6195,
        "orders": 0,
        "order_items": 0,
        "accounts": 0,
        "credit_notes": 0,
    }
