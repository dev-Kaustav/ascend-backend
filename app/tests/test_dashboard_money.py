"""The dashboard's money and stock figures (app/services/metrics.py): sales, collections and
orders for a period in India time, bucketed by week or month, and stock valued at landing price."""
from datetime import date, datetime, timezone
from decimal import Decimal

from app.models import SKU, Account, Brand, Inventory, Invoice, Order, Warehouse
from app.models.enums import InvoiceStatus, InvoiceType, OrderStatus, SupplyType
from app.services.metrics import money_series, stock_value as get_stock_value

TODAY = date(2026, 11, 17)  # a Tuesday; this week starts Monday 16 Nov


def _utc(y, m, d, hh=6, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def _order(db, created, status=OrderStatus.DELIVERED):
    order = Order(
        from_entity_type="WAREHOUSE", from_entity_id=1, to_entity_type="RETAILER", to_entity_id=1,
        status=status, created_at=created,
    )
    db.add(order)
    db.flush()
    return order


def _invoice(db, order, when, total):
    db.add(Invoice(
        invoice_number=f"INV-{order.id}", invoice_date=when, status=InvoiceStatus.ISSUED.value,
        order_id=order.id, invoice_type=InvoiceType.B2B.value, supply_type=SupplyType.REGULAR.value,
        reverse_charge=False, place_of_supply="Haryana", is_inter_state=False,
        supplier_legal_name="Ascend Foods", buyer_name="Shop", taxable_value=Decimal(total),
        discount_amount=Decimal("0"), cgst_amount=Decimal("0"), sgst_amount=Decimal("0"),
        igst_amount=Decimal("0"), cess_amount=Decimal("0"), total_tax_amount=Decimal("0"),
        grand_total=Decimal(total),
    ))


def _payment(db, order, when, amount, ref):
    db.add(Account(order_id=order.id, amount=Decimal(amount), transaction_reference=ref, collection_date=when))


def _seed_money(db):
    a = _order(db, _utc(2026, 11, 16))
    _invoice(db, a, _utc(2026, 11, 16), "1000.00")
    _payment(db, a, _utc(2026, 11, 17), "400.00", "p1")
    # 20:00 UTC on Sunday 15 Nov is 01:30 on Monday 16 Nov in India: this week, not last.
    b = _order(db, _utc(2026, 11, 15, 20, 0))
    _invoice(db, b, _utc(2026, 11, 15, 20, 0), "500.00")
    # Last week, and still this month.
    c = _order(db, _utc(2026, 11, 10))
    _invoice(db, c, _utc(2026, 11, 10), "250.00")
    # Last month, inside the same days (1st to 17th) and after them.
    d = _order(db, _utc(2026, 10, 5))
    _invoice(db, d, _utc(2026, 10, 5), "300.00")
    _payment(db, d, _utc(2026, 10, 6), "300.00", "p2")
    e = _order(db, _utc(2026, 10, 25))
    _invoice(db, e, _utc(2026, 10, 25), "999.00")
    # A cancelled order counts for nothing, even with an invoice and a payment against it.
    x = _order(db, _utc(2026, 11, 16), status=OrderStatus.CANCELLED)
    _invoice(db, x, _utc(2026, 11, 16), "7777.00")
    _payment(db, x, _utc(2026, 11, 16), "70.00", "px")
    db.commit()


def test_a_period_is_bucketed_by_week_in_india_time(db):
    _seed_money(db)

    money = money_series(db, "2026-11-01", "2026-11-17", today=TODAY)

    assert money["bucket"] == "week"
    assert money["totals"] == {"sales": 1750.0, "collected": 400.0, "orders": 3.0}
    # Sunday 1 Nov sits in the week of Monday 26 Oct; only the 1st counts towards it.
    assert [w["start"] for w in money["buckets"]] == ["2026-10-26", "2026-11-02", "2026-11-09", "2026-11-16"]
    assert money["buckets"][0] == {"start": "2026-10-26", "sales": 0.0, "collected": 0.0, "orders": 0.0}
    assert money["buckets"][-1] == {"start": "2026-11-16", "sales": 1500.0, "collected": 400.0, "orders": 2.0}
    # The 17 days before: 15 Oct to 31 Oct.
    assert money["previous"] == {"sales": 999.0, "collected": 0.0, "orders": 1.0}


def test_all_time_starts_at_the_first_record_and_has_no_comparison(db):
    _seed_money(db)

    money = money_series(db, today=TODAY)

    assert money["from"] == "2026-10-05"
    assert money["to"] == "2026-11-17"
    assert money["totals"] == {"sales": 3049.0, "collected": 700.0, "orders": 5.0}
    assert sum(w["sales"] for w in money["buckets"]) == 3049.0
    assert money["previous"] is None


def test_a_long_period_is_bucketed_by_month(db):
    _seed_money(db)

    money = money_series(db, "2026-01-01", "2026-11-17", today=TODAY)

    assert money["bucket"] == "month"
    assert money["buckets"][0]["start"] == "2026-01-01"
    assert money["buckets"][-1] == {"start": "2026-11-01", "sales": 1750.0, "collected": 400.0, "orders": 3.0}
    assert money["buckets"][-2] == {"start": "2026-10-01", "sales": 1299.0, "collected": 300.0, "orders": 2.0}


def test_stock_is_valued_at_landing_price_and_lowest_listed_first(db):
    brand = Brand(name="Jabsons")
    wh1, wh2 = Warehouse(name="City"), Warehouse(name="DLF")
    db.add_all([brand, wh1, wh2])
    db.flush()
    peanuts = SKU(name="Peanuts", brand_id=brand.id, distributor_landing_price=Decimal("7.50"))
    chana = SKU(name="Chana", brand_id=brand.id, distributor_landing_price=Decimal("8.00"))
    unpriced = SKU(name="Cashew", brand_id=brand.id)
    db.add_all([peanuts, chana, unpriced])
    db.flush()
    db.add_all([
        Inventory(sku_id=peanuts.id, warehouse_id=wh1.id, total_quantity=100),
        Inventory(sku_id=peanuts.id, warehouse_id=wh2.id, total_quantity=20),
        Inventory(sku_id=chana.id, warehouse_id=wh1.id, total_quantity=5),
        Inventory(sku_id=unpriced.id, warehouse_id=wh1.id, total_quantity=40),
    ])
    db.commit()

    stock = get_stock_value(db)

    assert stock["inventory_value"] == 120 * 7.5 + 5 * 8.0  # 940.0; the unpriced SKU adds nothing
    assert stock["unpriced_skus"] == 1
    assert [row["name"] for row in stock["lowest"]] == ["Chana", "Cashew", "Peanuts"]
    assert stock["lowest"][0]["units"] == 5.0


def test_no_priced_stock_is_unknown_not_zero(db):
    brand = Brand(name="Crax")
    db.add(brand)
    db.flush()
    db.add(SKU(name="Rings", brand_id=brand.id))
    db.commit()
    assert get_stock_value(db)["inventory_value"] is None
