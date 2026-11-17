"""One business, one set of numbers. Every screen that shows a figure gets it from
app/services/metrics.py, so the same figure must come out the same everywhere, and a cancelled
order must count in none of them. If one of these fails, two screens disagree."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.models import SKU, Account, Brand, Invoice, Order, OrderItem, OrderItemTax, Retailer
from app.models.enums import InvoiceStatus, InvoiceType, OrderStatus, PaymentStatus, SupplyType
from app.services import metrics
from app.services.accounting import list_payments
from app.services.admin import get_admin_summary, get_order_status_summary, get_orders_page, get_orders_value
from app.services.daily_sales import get_daily_sales_summary
from app.services.finance import calculate_order_totals
from app.services.outstanding import get_outstanding_orders, get_outstanding_summary

ADMIN = SimpleNamespace(role="ADMIN", employee_id=None)
WHEN = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)


def _seed(db):
    brand = Brand(name="Jabsons")
    db.add(brand)
    db.flush()
    sku = SKU(name="Peanuts", brand_id=brand.id)
    shop = Retailer(name="Gupta Stores")
    db.add_all([sku, shop])
    db.flush()

    def order(status, payment_status, quantity, unit_price, paid=None, invoiced=False):
        o = Order(
            from_entity_type="WAREHOUSE", from_entity_id=1, to_entity_type="RETAILER", to_entity_id=shop.id,
            status=status, payment_status=payment_status, created_at=WHEN,
        )
        db.add(o)
        db.flush()
        item = OrderItem(order_id=o.id, sku_id=sku.id, quantity=quantity, unit_price=Decimal(unit_price), discount_amount=0)
        db.add(item)
        db.flush()
        db.add(OrderItemTax(order_item_id=item.id, tax_type="IGST", rate=Decimal("5")))
        if paid:
            db.add(Account(order_id=o.id, amount=Decimal(paid), transaction_reference=f"p{o.id}", collection_date=WHEN))
        if invoiced:
            db.add(Invoice(
                invoice_number=f"INV-{o.id}", invoice_date=WHEN, status=InvoiceStatus.ISSUED.value, order_id=o.id,
                invoice_type=InvoiceType.B2B.value, supply_type=SupplyType.REGULAR.value, reverse_charge=False,
                place_of_supply="Haryana", is_inter_state=True, supplier_legal_name="Ascend Foods", buyer_name="Shop",
                taxable_value=Decimal("0"), discount_amount=Decimal("0"), cgst_amount=Decimal("0"),
                sgst_amount=Decimal("0"), igst_amount=Decimal("0"), cess_amount=Decimal("0"),
                total_tax_amount=Decimal("0"), grand_total=Decimal(quantity) * Decimal(unit_price),
            ))
        db.commit()
        return o

    order(OrderStatus.DELIVERED, PaymentStatus.PAID, 3, "33.33", paid="99.99", invoiced=True)
    order(OrderStatus.DELIVERED, PaymentStatus.PARTIAL, 2, "100.00", paid="50.00", invoiced=True)
    order(OrderStatus.DELIVERED, PaymentStatus.CREDIT, 1, "120.00", invoiced=True)
    order(OrderStatus.PENDING, PaymentStatus.CREDIT, 4, "10.00")
    # Cancelled after money and an invoice were recorded against it.
    order(OrderStatus.CANCELLED, PaymentStatus.PARTIAL, 10, "1000.00", paid="500.00", invoiced=True)


def test_what_shops_owe_is_the_same_on_every_screen(db):
    _seed(db)
    owed = 150.0 + 120.0  # the part-paid order's balance and the credit order; nothing else owes

    rows, _ = get_outstanding_orders(db)
    assert get_outstanding_summary(db)["total_outstanding"] == owed
    assert get_admin_summary(db)["orders"]["outstanding"] == owed
    assert float(sum(row["outstanding"] for row in rows)) == owed


def test_a_cancelled_order_counts_in_no_total(db):
    _seed(db)
    live_value = 99.99 + 200.0 + 120.0 + 40.0
    sales = 99.99 + 200.0 + 120.0
    collected = 99.99 + 50.0

    summary = get_admin_summary(db)
    assert summary["orders"]["total"] == 4
    assert summary["orders"]["revenue"] == live_value
    assert sum(summary["orders"]["payment_status"].values()) == 4
    assert summary["money"]["totals"] == {"sales": sales, "collected": collected, "orders": 4.0}

    assert metrics.sales(db) == sales
    assert metrics.collected(db) == collected
    assert get_orders_value(db, ADMIN) == live_value
    _, listed = get_orders_page(db, ADMIN)
    assert listed == 4 == get_order_status_summary(db)["ALL"]

    payments, count, total_amount, _ = list_payments(db)
    assert count == 2 and total_amount == collected
    assert float(sum(p.amount for p in payments)) == collected
    assert {p.retailer_name for p in payments} == {"Gupta Stores"}

    daily = get_daily_sales_summary(db)
    assert daily["total_orders"] == 4
    assert float(daily["total_amount"]) == live_value
    assert float(daily["total_collected"]) == collected
    assert daily["total_cancelled"] == 1  # counted on its own, in no total


def test_money_on_cancelled_orders_is_reported_to_be_put_right(db):
    _seed(db)
    assert metrics.cancelled_money(db) == {"orders": 1, "payments": 1, "payments_total": 500.0, "invoices": 1}


def test_the_sql_order_total_matches_the_invoice_arithmetic(db):
    _seed(db)
    sub = metrics.order_total_subquery(db)
    sql_totals = dict(
        db.query(Order.id, metrics.order_total_expr(sub)).outerjoin(sub, sub.c.order_id == Order.id).all()
    )
    for order in db.query(Order):
        assert round(float(sql_totals[order.id]), 2) == float(calculate_order_totals(order)["grand_total"])
