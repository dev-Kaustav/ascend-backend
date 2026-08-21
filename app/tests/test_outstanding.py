"""Brings `app/services/outstanding.py` (the receivables report) under test, exactly as it
is. Only its two public functions, `get_outstanding_orders` and `get_outstanding_summary`,
are ever called here -- never `_base_outstanding_query` or `_parse_date` directly, which are
implementation (D4/D5, 05-CONTEXT.md).

Every order in this module is built directly (`Order` + `OrderItem` + `OrderItemTax(rate=0)`),
never through the order service's inventory-allocating creation path -- that path allocates stock, and Phase 4 made
allocation refuse expired batches, which this report never reads and does not need. Every
tax rate is 0 so grand totals are hand-computable arithmetic; the GST formula itself is
unverified with the CA and out of scope here (05-CONTEXT.md).

`outstanding.py` reads `datetime.utcnow()` directly with no injectable seam, so every test
that cares about elapsed time freezes it via `monkeypatch.setattr("app.services.outstanding.
datetime", _FrozenDateTime)` -- a `datetime` subclass, not a stub, because `_parse_date` also
calls `datetime.combine` and `datetime.min` through the same name.

RPT-06 (the naive/aware `datetime.utcnow() - order.created_at` crash under real PostgreSQL)
was FIXED in `outstanding.py` ahead of this module, per Phase 5 coordinator ruling R5
(05-CONTEXT.md), which overrides D5 for that one defect only -- see
`app/tests/test_outstanding_pg.py` for the real-PostgreSQL proof. RPT-07 (the `total`/
`len(items)` mismatch) is a separate, still-unfixed defect and is pinned below by a named,
passing test per D5 -- not fixed, not hidden behind a skip or an expected-failure marker.
"""

import itertools
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.models import Account, Brand, CreditNote, CreditNoteItem, Employee, Order, OrderItem, OrderItemTax, Retailer, SKU
from app.models.enums import EmployeeRole, OrderStatus, PaymentMode, PaymentStatus
from app.services.finance import calculate_order_outstanding
from app.services.outstanding import get_outstanding_orders, get_outstanding_summary

# Frozen "now" for every test in this module. Picked well clear of any aging bucket edge
# (7/15/30 days) so a scenario built against it reads at a glance.
_FROZEN_NOW = datetime(2026, 6, 15, 12, 0, 0)


class _FrozenDateTime(datetime):
    """A subclass, not a Mock: `outstanding.py` calls `datetime.combine` and `datetime.min`
    through the same module-level `datetime` name (`_parse_date`), and a bare stub object
    would break those. `utcnow()` is the only method this module overrides."""

    @classmethod
    def utcnow(cls):
        return _FROZEN_NOW


def _freeze(monkeypatch):
    monkeypatch.setattr("app.services.outstanding.datetime", _FrozenDateTime)


_txn_seq = itertools.count()
_cn_seq = itertools.count()


def _seed(db):
    """One Brand, one SKU, two Retailer rows, two Employee rows (role=SALESMAN). Reused by
    every test in this module via `_order`/`_credit_note`."""
    brand = Brand(name="Outstanding Test Brand")
    db.add(brand)
    db.flush()
    sku = SKU(name="Outstanding Test SKU", brand_id=brand.id)
    db.add(sku)
    db.flush()
    retailer_a = Retailer(name="Retailer A", state="Haryana")
    retailer_b = Retailer(name="Retailer B", state="Haryana")
    salesman_a = Employee(name="Salesman A", email="outstanding-salesman-a@ascend.test", role=EmployeeRole.SALESMAN)
    salesman_b = Employee(name="Salesman B", email="outstanding-salesman-b@ascend.test", role=EmployeeRole.SALESMAN)
    db.add_all([retailer_a, retailer_b, salesman_a, salesman_b])
    db.commit()
    return SimpleNamespace(
        sku_id=sku.id,
        retailer_a=retailer_a.id,
        retailer_b=retailer_b.id,
        salesman_a=salesman_a.id,
        salesman_b=salesman_b.id,
    )


def _order(
    db,
    *,
    retailer_id,
    sku_id,
    salesman_id=None,
    warehouse_id=1,
    unit_price=100,
    quantity=1,
    status=OrderStatus.DELIVERED,
    payment_status=PaymentStatus.PARTIAL,
    created_at=None,
    paid=None,
):
    """Builds `Order` + `OrderItem` + `OrderItemTax(rate=0)` the way `test_accounting.py:9-37`
    does -- direct construction, never the inventory-allocating order service path. `created_at` defaults to the
    frozen "now" rather than the wall clock (`created_at` is a `server_default`); pass it
    explicitly whenever a test's expectation depends on the value."""
    order = Order(
        from_entity_type="WAREHOUSE",
        from_entity_id=warehouse_id,
        to_entity_type="RETAILER",
        to_entity_id=retailer_id,
        status=status,
        payment_status=payment_status,
        salesman_id=salesman_id,
        created_at=created_at if created_at is not None else _FROZEN_NOW,
    )
    db.add(order)
    db.flush()
    item = OrderItem(order_id=order.id, sku_id=sku_id, quantity=quantity, unit_price=unit_price, discount_amount=0)
    db.add(item)
    db.flush()
    db.add(OrderItemTax(order_item_id=item.id, tax_type="GST", rate=0))
    if paid is not None:
        db.add(Account(order_id=order.id, amount=paid, transaction_reference=f"txn-{next(_txn_seq)}"))
    db.commit()
    return order


def _credit_note(db, *, order, sku_id, unit_price=100, quantity=1, applies_to_outstanding=True):
    cn = CreditNote(
        order_id=order.id,
        credit_note_number=f"CN-{next(_cn_seq)}",
        applies_to_outstanding=applies_to_outstanding,
    )
    db.add(cn)
    db.flush()
    db.add(CreditNoteItem(credit_note_id=cn.id, sku_id=sku_id, quantity=quantity, unit_price=unit_price))
    db.commit()
    return cn


# --- get_outstanding_orders -------------------------------------------------------------


def test_delivered_credit_order_with_balance_appears_in_list(db, monkeypatch):
    _freeze(monkeypatch)
    seed = _seed(db)
    order = _order(
        db, retailer_id=seed.retailer_a, sku_id=seed.sku_id,
        status=OrderStatus.DELIVERED, payment_status=PaymentStatus.CREDIT, created_at=_FROZEN_NOW,
    )
    items, total = get_outstanding_orders(db)
    assert total == 1
    assert len(items) == 1
    assert items[0]["order_id"] == order.id


def test_pending_order_with_identical_balance_does_not_appear(db, monkeypatch):
    _freeze(monkeypatch)
    seed = _seed(db)
    _order(
        db, retailer_id=seed.retailer_a, sku_id=seed.sku_id,
        status=OrderStatus.PENDING, payment_status=PaymentStatus.CREDIT, created_at=_FROZEN_NOW,
    )
    items, total = get_outstanding_orders(db)
    assert items == []
    assert total == 0


def test_delivered_paid_order_with_balance_still_owing_does_not_appear(db, monkeypatch):
    """Flagged PAID even though only partially paid: the entry filter keys off
    `payment_status`, not the actual balance (`outstanding.py:26-27`) -- this order is
    invisible to the whole report regardless of what it actually owes."""
    _freeze(monkeypatch)
    seed = _seed(db)
    _order(
        db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, unit_price=100, quantity=1,
        status=OrderStatus.DELIVERED, payment_status=PaymentStatus.PAID, paid=30, created_at=_FROZEN_NOW,
    )
    items, total = get_outstanding_orders(db)
    assert items == []
    assert total == 0


def test_outstanding_is_billed_minus_paid(db, monkeypatch):
    _freeze(monkeypatch)
    seed = _seed(db)
    _order(
        db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, unit_price=100, quantity=1,
        paid=30, payment_status=PaymentStatus.PARTIAL, created_at=_FROZEN_NOW,
    )
    items, _total = get_outstanding_orders(db)
    assert len(items) == 1
    assert items[0]["outstanding"] == Decimal("70.00")


def test_credit_note_applies_to_outstanding_flag_is_respected(db, monkeypatch):
    """Both credit notes on the SAME order: the True one (20) must reduce the balance, the
    False one (15) must not -- asserted together, not in two separate scenarios."""
    _freeze(monkeypatch)
    seed = _seed(db)
    order = _order(
        db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, unit_price=100, quantity=1,
        payment_status=PaymentStatus.CREDIT, created_at=_FROZEN_NOW,
    )
    _credit_note(db, order=order, sku_id=seed.sku_id, unit_price=20, applies_to_outstanding=True)
    _credit_note(db, order=order, sku_id=seed.sku_id, unit_price=15, applies_to_outstanding=False)
    items, _total = get_outstanding_orders(db)
    assert len(items) == 1
    assert items[0]["outstanding"] == Decimal("80.00")
    assert items[0]["credit_note_total"] == Decimal("20.00")


def test_payments_exceeding_total_clamp_outstanding_to_zero_and_drop_order(db, monkeypatch):
    _freeze(monkeypatch)
    seed = _seed(db)
    order = _order(
        db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, unit_price=100, quantity=1,
        paid=150, payment_status=PaymentStatus.PARTIAL, created_at=_FROZEN_NOW,
    )
    assert calculate_order_outstanding(order) == Decimal("0.00")
    items, _total = get_outstanding_orders(db)
    assert items == []


def test_total_counts_orders_the_page_then_drops_them(db, monkeypatch):
    """Pins `get_outstanding_orders`' `total = query.count()` call (`outstanding.py`, entry
    filter query before the `outstanding <= 0` skip) as a KNOWN, DELIBERATELY UNFIXED
    DEFECT -- `#todo RPT-07` -- per 05-CONTEXT.md D5. The Phase 5 coordinator ruling (R5)
    fixes RPT-06 in this same plan but explicitly leaves RPT-07 pinned, not fixed.

    `total` is computed against the entry-filtered query (DELIVERED status, payment_status
    in CREDIT/PARTIAL) BEFORE the loop that skips rows whose computed `outstanding <= 0`. An
    order flagged PARTIAL that has in fact been paid in full (its balance is zero) matches
    the entry filter and is counted in `total`, but is dropped from `items`. A paginated
    caller sees a `total` it can never reach by paging through `items`.

    Reachable in production: `excel_import._reconcile_payment_status`
    (`excel_import.py:541-547`) sets `PARTIAL` from the mere presence of a payment, not from
    whether a balance remains -- an imported, fully-recovered bill can sit in exactly this
    state.

    When someone is authorised to fix RPT-07, this assertion must be INVERTED
    (`total == len(items)`) -- do not delete this test, flip it.
    """
    _freeze(monkeypatch)
    seed = _seed(db)
    _order(
        db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, unit_price=100, quantity=1,
        paid=30, payment_status=PaymentStatus.PARTIAL, created_at=_FROZEN_NOW,
    )
    _order(
        db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, unit_price=100, quantity=1,
        paid=100, payment_status=PaymentStatus.PARTIAL, created_at=_FROZEN_NOW,
    )
    items, total = get_outstanding_orders(db)
    assert total == 2
    assert len(items) == 1


def test_retailer_id_narrows_to_expected_orders(db, monkeypatch):
    _freeze(monkeypatch)
    seed = _seed(db)
    order_a = _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, created_at=_FROZEN_NOW)
    _order(db, retailer_id=seed.retailer_b, sku_id=seed.sku_id, created_at=_FROZEN_NOW)
    items, total = get_outstanding_orders(db, retailer_id=seed.retailer_a)
    assert total == 1
    assert [i["order_id"] for i in items] == [order_a.id]


def test_salesman_id_narrows_to_expected_orders(db, monkeypatch):
    _freeze(monkeypatch)
    seed = _seed(db)
    order_a = _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, salesman_id=seed.salesman_a, created_at=_FROZEN_NOW)
    _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, salesman_id=seed.salesman_b, created_at=_FROZEN_NOW)
    items, total = get_outstanding_orders(db, salesman_id=seed.salesman_a)
    assert total == 1
    assert [i["order_id"] for i in items] == [order_a.id]


def test_warehouse_id_narrows_to_expected_orders(db, monkeypatch):
    _freeze(monkeypatch)
    seed = _seed(db)
    order_a = _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, warehouse_id=1, created_at=_FROZEN_NOW)
    _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, warehouse_id=2, created_at=_FROZEN_NOW)
    items, total = get_outstanding_orders(db, warehouse_id=1)
    assert total == 1
    assert [i["order_id"] for i in items] == [order_a.id]


def test_date_range_is_half_open(db, monkeypatch):
    """`to_date` filters `created_at < midnight of the following day` (`_parse_date`,
    `is_end=True`): an order at 23:59:59 on `to_date` is in, one at 00:00:00 the next day is
    out."""
    _freeze(monkeypatch)
    seed = _seed(db)
    in_range = _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, created_at=datetime(2026, 6, 15, 23, 59, 59))
    out_of_range = _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, created_at=datetime(2026, 6, 16, 0, 0, 0))
    items, total = get_outstanding_orders(db, from_date="2026-06-10", to_date="2026-06-15")
    ids = {i["order_id"] for i in items}
    assert in_range.id in ids
    assert out_of_range.id not in ids
    assert total == 1


def test_limit_and_offset_page_newest_first(db, monkeypatch):
    _freeze(monkeypatch)
    seed = _seed(db)
    oldest = _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, created_at=_FROZEN_NOW - timedelta(days=3))
    middle = _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, created_at=_FROZEN_NOW - timedelta(days=2))
    newest = _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, created_at=_FROZEN_NOW - timedelta(days=1))
    page1, total = get_outstanding_orders(db, limit=2, offset=0)
    page2, _total2 = get_outstanding_orders(db, limit=2, offset=2)
    assert total == 3
    assert [i["order_id"] for i in page1] == [newest.id, middle.id]
    assert [i["order_id"] for i in page2] == [oldest.id]


def test_payment_modes_groups_amounts_and_null_mode_is_other(db, monkeypatch):
    _freeze(monkeypatch)
    seed = _seed(db)
    order = _order(
        db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, unit_price=100, quantity=1,
        payment_status=PaymentStatus.PARTIAL, created_at=_FROZEN_NOW,
    )
    db.add(Account(order_id=order.id, amount=20, transaction_reference="txn-mode-cash", payment_mode=PaymentMode.CASH))
    db.add(Account(order_id=order.id, amount=10, transaction_reference="txn-mode-null", payment_mode=None))
    db.commit()
    items, _total = get_outstanding_orders(db)
    assert len(items) == 1
    assert items[0]["payment_modes"] == {"CASH": Decimal("20"), "OTHER": Decimal("10")}


def test_retailer_and_salesman_name_resolve_null_salesman_yields_null_name(db, monkeypatch):
    _freeze(monkeypatch)
    seed = _seed(db)
    _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, salesman_id=None, created_at=_FROZEN_NOW)
    items, _total = get_outstanding_orders(db)
    assert items[0]["retailer_name"] == "Retailer A"
    assert items[0]["salesman_name"] is None
    assert items[0]["salesman_id"] is None


def test_money_fields_are_decimal_not_float(db, monkeypatch):
    """A scenario with both a payment and an applicable credit note, so none of the four
    money fields defaults to `int 0` via a bare `sum()` -- the case that would let a
    0.1 + 0.2 style float back in unnoticed."""
    _freeze(monkeypatch)
    seed = _seed(db)
    order = _order(
        db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, unit_price=100, quantity=1,
        paid=10, payment_status=PaymentStatus.PARTIAL, created_at=_FROZEN_NOW,
    )
    _credit_note(db, order=order, sku_id=seed.sku_id, unit_price=5, applies_to_outstanding=True)
    items, _total = get_outstanding_orders(db)
    item = items[0]
    for field in ("grand_total", "payments_total", "credit_note_total", "outstanding"):
        assert isinstance(item[field], Decimal), f"{field} is {type(item[field])}, not Decimal"
    assert item["outstanding"] == Decimal("85.00")


# --- get_outstanding_summary ------------------------------------------------------------


def test_summary_aging_buckets_hit_exact_boundaries_and_sum_to_total(db, monkeypatch):
    _freeze(monkeypatch)
    seed = _seed(db)
    scenarios = [
        (7, 10),
        (8, 20),
        (15, 30),
        (16, 40),
        (30, 50),
        (31, 60),
    ]
    for days_old, amount in scenarios:
        _order(
            db,
            retailer_id=seed.retailer_a,
            sku_id=seed.sku_id,
            unit_price=amount,
            quantity=1,
            payment_status=PaymentStatus.CREDIT,
            created_at=_FROZEN_NOW - timedelta(days=days_old),
        )

    summary = get_outstanding_summary(db)

    assert summary["aging"] == {
        "0_7": Decimal("10.00"),
        "7_15": Decimal("50.00"),
        "15_30": Decimal("90.00"),
        "30_plus": Decimal("60.00"),
    }
    assert sum(summary["aging"].values(), Decimal("0.00")) == summary["total_outstanding"]
    assert summary["total_outstanding"] == Decimal("210.00")


def test_summary_by_salesman_uses_pending_sort_and_unassigned_bucket(db, monkeypatch):
    _freeze(monkeypatch)
    seed = _seed(db)
    _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, salesman_id=seed.salesman_a, unit_price=100, paid=10, created_at=_FROZEN_NOW)
    _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, salesman_id=seed.salesman_a, unit_price=60, created_at=_FROZEN_NOW)
    _order(db, retailer_id=seed.retailer_b, sku_id=seed.sku_id, salesman_id=seed.salesman_b, unit_price=200, created_at=_FROZEN_NOW)
    _order(db, retailer_id=seed.retailer_b, sku_id=seed.sku_id, salesman_id=None, unit_price=25, created_at=_FROZEN_NOW)

    rows = get_outstanding_summary(db)["by_salesman"]

    assert [row["salesman_name"] for row in rows] == ["Salesman B", "Salesman A", "Unassigned"]
    assert rows[0] == {
        "salesman_id": seed.salesman_b,
        "salesman_name": "Salesman B",
        "total_credit": Decimal("200.00"),
        "recovered": Decimal("0.00"),
        "pending": Decimal("200.00"),
        "bills": 1,
    }
    assert rows[1] == {
        "salesman_id": seed.salesman_a,
        "salesman_name": "Salesman A",
        "total_credit": Decimal("160.00"),
        "recovered": Decimal("10.00"),
        "pending": Decimal("150.00"),
        "bills": 2,
    }
    assert rows[2]["salesman_id"] == 0
    assert rows[2]["pending"] == Decimal("25.00")


def test_summary_by_retailer_carries_amounts_and_resolves_names(db, monkeypatch):
    _freeze(monkeypatch)
    seed = _seed(db)
    _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, unit_price=100, paid=40, created_at=_FROZEN_NOW)
    _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, unit_price=25, created_at=_FROZEN_NOW)
    _order(db, retailer_id=seed.retailer_b, sku_id=seed.sku_id, unit_price=80, paid=10, created_at=_FROZEN_NOW)

    rows = get_outstanding_summary(db)["by_retailer"]
    by_id = {row["retailer_id"]: row for row in rows}

    assert by_id[seed.retailer_a] == {
        "retailer_id": seed.retailer_a,
        "retailer_name": "Retailer A",
        "total_credit": Decimal("125.00"),
        "recovered": Decimal("40.00"),
        "pending": Decimal("85.00"),
        "bills": 2,
    }
    assert by_id[seed.retailer_b] == {
        "retailer_id": seed.retailer_b,
        "retailer_name": "Retailer B",
        "total_credit": Decimal("80.00"),
        "recovered": Decimal("10.00"),
        "pending": Decimal("70.00"),
        "bills": 1,
    }


def test_summary_missing_retailer_row_is_labelled_unknown(db, monkeypatch):
    _freeze(monkeypatch)
    seed = _seed(db)
    missing_retailer_id = 999999
    assert db.query(Retailer).filter(Retailer.id == missing_retailer_id).first() is None
    _order(db, retailer_id=missing_retailer_id, sku_id=seed.sku_id, unit_price=100, created_at=_FROZEN_NOW)

    rows = get_outstanding_summary(db)["by_retailer"]

    assert rows == [{
        "retailer_id": missing_retailer_id,
        "retailer_name": "Unknown",
        "total_credit": Decimal("100.00"),
        "recovered": Decimal("0.00"),
        "pending": Decimal("100.00"),
        "bills": 1,
    }]


def test_summary_total_credit_notes_counts_only_applicable_notes(db, monkeypatch):
    _freeze(monkeypatch)
    seed = _seed(db)
    order = _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, unit_price=100, created_at=_FROZEN_NOW)
    _credit_note(db, order=order, sku_id=seed.sku_id, unit_price=20, applies_to_outstanding=True)
    _credit_note(db, order=order, sku_id=seed.sku_id, unit_price=15, applies_to_outstanding=False)

    summary = get_outstanding_summary(db)

    assert summary["total_credit_notes"] == Decimal("20.00")
    assert summary["total_outstanding"] == Decimal("80.00")


def test_summary_recovery_rate_is_recovered_over_recovered_plus_outstanding(db, monkeypatch):
    _freeze(monkeypatch)
    seed = _seed(db)
    _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, unit_price=100, paid=25, created_at=_FROZEN_NOW)

    summary = get_outstanding_summary(db)

    assert summary["total_recovered"] == Decimal("25.00")
    assert summary["total_outstanding"] == Decimal("75.00")
    assert summary["recovery_rate"] == 25.0


def test_summary_warehouse_and_date_filters_narrow_the_report(db, monkeypatch):
    _freeze(monkeypatch)
    seed = _seed(db)
    in_range = _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, warehouse_id=1, unit_price=100, created_at=datetime(2026, 6, 10, 12, 0, 0))
    _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, warehouse_id=1, unit_price=50, created_at=datetime(2026, 6, 9, 23, 59, 59))
    _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, warehouse_id=1, unit_price=60, created_at=datetime(2026, 6, 16, 0, 0, 0))
    _order(db, retailer_id=seed.retailer_a, sku_id=seed.sku_id, warehouse_id=2, unit_price=70, created_at=datetime(2026, 6, 10, 12, 0, 0))

    summary = get_outstanding_summary(db, warehouse_id=1, from_date="2026-06-10", to_date="2026-06-15")

    assert summary["total_outstanding"] == Decimal("100.00")
    assert summary["by_retailer"][0]["bills"] == 1
    assert summary["by_retailer"][0]["retailer_id"] == seed.retailer_a
    assert in_range.id is not None


def test_summary_zero_qualifying_orders_returns_zeroes_not_division_error(db, monkeypatch):
    _freeze(monkeypatch)
    seed = _seed(db)
    _order(
        db,
        retailer_id=seed.retailer_a,
        sku_id=seed.sku_id,
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.CREDIT,
        created_at=_FROZEN_NOW,
    )

    summary = get_outstanding_summary(db)

    assert summary == {
        "total_outstanding": Decimal("0.00"),
        "total_recovered": Decimal("0.00"),
        "total_credit_notes": Decimal("0.00"),
        "recovery_rate": 0,
        "aging": {"0_7": 0, "7_15": 0, "15_30": 0, "30_plus": 0},
        "by_salesman": [],
        "by_retailer": [],
    }
