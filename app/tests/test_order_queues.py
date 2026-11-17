"""The orders screen's queues: the status summary's PAYMENT_DUE count and the matching list filter."""
from types import SimpleNamespace

from app.models import Account, Brand, Order, OrderItem, SKU
from app.models.enums import OrderStatus, PaymentStatus
from app.services.admin import PAYMENT_DUE, get_order_status_summary, get_orders_page

ADMIN = SimpleNamespace(role="ADMIN", employee_id=None)


def _order(db, status, payment_status, amount=100):
    sku = db.query(SKU).first()
    if sku is None:
        brand = Brand(name="Queue brand")
        db.add(brand)
        db.flush()
        sku = SKU(name="Queue SKU", brand_id=brand.id)
        db.add(sku)
        db.flush()
    order = Order(
        from_entity_type="WAREHOUSE", from_entity_id=1, to_entity_type="RETAILER", to_entity_id=1,
        status=status, payment_status=payment_status,
    )
    db.add(order)
    db.flush()
    db.add(OrderItem(order_id=order.id, sku_id=sku.id, quantity=1, unit_price=amount, discount_amount=0))
    if payment_status in (PaymentStatus.PAID, PaymentStatus.PARTIAL):
        db.add(Account(order_id=order.id, amount=amount if payment_status == PaymentStatus.PAID else amount / 2, transaction_reference=f"queue-{order.id}"))
    db.commit()
    return order


def test_payment_due_counts_live_orders_that_still_owe(db):
    _order(db, OrderStatus.DELIVERED, PaymentStatus.CREDIT)
    _order(db, OrderStatus.DELIVERED, PaymentStatus.PARTIAL)
    _order(db, OrderStatus.DELIVERED, PaymentStatus.UNPAID)
    _order(db, OrderStatus.DELIVERED, PaymentStatus.PAID)
    # Money is owed before delivery too; cancelled orders never count.
    _order(db, OrderStatus.OUT_FOR_DELIVERY, PaymentStatus.CREDIT)

    _order(db, OrderStatus.CANCELLED, PaymentStatus.CREDIT)
    summary = get_order_status_summary(db)

    assert summary["DELIVERED"] == 4
    assert summary["OUT_FOR_DELIVERY"] == 1
    assert summary["PAYMENT_DUE"] == 4


def test_payment_due_is_zero_not_missing(db):
    _order(db, OrderStatus.DELIVERED, PaymentStatus.PAID)
    assert get_order_status_summary(db)["PAYMENT_DUE"] == 0


def test_the_list_filter_matches_the_count(db):
    owing = {
        _order(db, OrderStatus.DELIVERED, PaymentStatus.CREDIT).id,
        _order(db, OrderStatus.DELIVERED, PaymentStatus.PARTIAL).id,
    }
    _order(db, OrderStatus.DELIVERED, PaymentStatus.PAID)
    owing.add(_order(db, OrderStatus.PENDING, PaymentStatus.CREDIT).id)

    items, total = get_orders_page(db, ADMIN, payment_status=PAYMENT_DUE)

    assert total == 3 == get_order_status_summary(db)["PAYMENT_DUE"]
    assert {item.id for item in items} == owing


def test_one_salesmans_queues_list_and_value(db):
    from app.models import SKU, Brand, Employee, OrderItem
    from app.models.enums import EmployeeRole
    from app.services.admin import get_orders_value

    brand = Brand(name="Jabsons")
    db.add(brand)
    db.flush()
    sku = SKU(name="Peanuts", brand_id=brand.id)
    sumit = Employee(name="Sumit", email="sumit-q@ascend.com", role=EmployeeRole.SALESMAN)
    nitin = Employee(name="Nitin", email="nitin-q@ascend.com", role=EmployeeRole.SALESMAN)
    db.add_all([sku, sumit, nitin])
    db.flush()

    def sold(by, status, payment, amount):
        order = _order(db, status, payment, amount=amount)
        order.salesman_id = by.id
        db.commit()
        return order

    sold(sumit, OrderStatus.PENDING, PaymentStatus.CREDIT, 100)
    sold(sumit, OrderStatus.DELIVERED, PaymentStatus.CREDIT, 250)
    sold(sumit, OrderStatus.CANCELLED, PaymentStatus.CREDIT, 999)
    sold(nitin, OrderStatus.PENDING, PaymentStatus.CREDIT, 40)

    summary = get_order_status_summary(db, salesman_id=sumit.id)
    assert summary == {"PENDING": 1, "DELIVERED": 1, "CANCELLED": 1, "ALL": 2, "PAYMENT_DUE": 2}

    # The list leaves the cancelled order out unless cancelled orders are asked for.
    items, total = get_orders_page(db, ADMIN, salesman_id=sumit.id)
    assert total == 2 == summary["ALL"]
    assert {item.salesman_id for item in items} == {sumit.id}
    _, cancelled_total = get_orders_page(db, ADMIN, status="CANCELLED", salesman_id=sumit.id)
    assert cancelled_total == 1

    # Real business only: the cancelled order is not counted unless cancelled orders are listed.
    assert get_orders_value(db, ADMIN, salesman_id=sumit.id) == 350.0
    assert get_orders_value(db, ADMIN, status="CANCELLED", salesman_id=sumit.id) == 999.0
    assert get_orders_value(db, ADMIN, salesman_id=nitin.id) == 40.0


def test_payment_due_uses_actual_balance_instead_of_stale_payment_labels(db):
    settled = _order(db, OrderStatus.DELIVERED, PaymentStatus.PARTIAL)
    db.add(Account(order_id=settled.id, amount=50, transaction_reference=f"settled-{settled.id}"))
    stale_paid = _order(db, OrderStatus.PENDING, PaymentStatus.PAID)
    stale_paid.payments[0].amount = 20
    db.commit()
    items, total = get_orders_page(db, ADMIN, payment_status=PAYMENT_DUE)
    assert total == 1 == get_order_status_summary(db)["PAYMENT_DUE"]
    assert [order.id for order in items] == [stale_paid.id]
