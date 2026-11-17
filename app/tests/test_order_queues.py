"""The orders screen's queues: the status summary's PAYMENT_DUE count and the matching list filter."""
from types import SimpleNamespace

from app.models import Order
from app.models.enums import OrderStatus, PaymentStatus
from app.services.admin import PAYMENT_DUE, get_order_status_summary, get_orders_page

ADMIN = SimpleNamespace(role="ADMIN", employee_id=None)


def _order(db, status, payment_status):
    order = Order(
        from_entity_type="WAREHOUSE",
        from_entity_id=1,
        to_entity_type="RETAILER",
        to_entity_id=1,
        status=status,
        payment_status=payment_status,
    )
    db.add(order)
    db.commit()
    return order


def test_payment_due_counts_delivered_orders_that_still_owe(db):
    _order(db, OrderStatus.DELIVERED, PaymentStatus.CREDIT)
    _order(db, OrderStatus.DELIVERED, PaymentStatus.PARTIAL)
    _order(db, OrderStatus.DELIVERED, PaymentStatus.UNPAID)
    _order(db, OrderStatus.DELIVERED, PaymentStatus.PAID)
    # Owing, but not delivered yet: in its own status queue, not the collections one.
    _order(db, OrderStatus.OUT_FOR_DELIVERY, PaymentStatus.CREDIT)

    summary = get_order_status_summary(db)

    assert summary["DELIVERED"] == 4
    assert summary["OUT_FOR_DELIVERY"] == 1
    assert summary["PAYMENT_DUE"] == 3


def test_payment_due_is_zero_not_missing(db):
    _order(db, OrderStatus.DELIVERED, PaymentStatus.PAID)
    assert get_order_status_summary(db)["PAYMENT_DUE"] == 0


def test_the_list_filter_matches_the_count(db):
    owing = {
        _order(db, OrderStatus.DELIVERED, PaymentStatus.CREDIT).id,
        _order(db, OrderStatus.DELIVERED, PaymentStatus.PARTIAL).id,
    }
    _order(db, OrderStatus.DELIVERED, PaymentStatus.PAID)
    _order(db, OrderStatus.PENDING, PaymentStatus.CREDIT)

    items, total = get_orders_page(db, ADMIN, status="DELIVERED", payment_status=PAYMENT_DUE)

    assert total == 2 == get_order_status_summary(db)["PAYMENT_DUE"]
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
        order = _order(db, status, payment)
        order.salesman_id = by.id
        db.add(OrderItem(order_id=order.id, sku_id=sku.id, quantity=1, unit_price=amount, discount_amount=0))
        db.commit()
        return order

    sold(sumit, OrderStatus.PENDING, PaymentStatus.CREDIT, 100)
    sold(sumit, OrderStatus.DELIVERED, PaymentStatus.CREDIT, 250)
    sold(sumit, OrderStatus.CANCELLED, PaymentStatus.CREDIT, 999)
    sold(nitin, OrderStatus.PENDING, PaymentStatus.CREDIT, 40)

    summary = get_order_status_summary(db, salesman_id=sumit.id)
    assert summary == {"PENDING": 1, "DELIVERED": 1, "CANCELLED": 1, "PAYMENT_DUE": 1}

    items, total = get_orders_page(db, ADMIN, salesman_id=sumit.id)
    assert total == 3
    assert {item.salesman_id for item in items} == {sumit.id}

    # Real business only: the cancelled order is not counted unless cancelled orders are listed.
    assert get_orders_value(db, ADMIN, salesman_id=sumit.id) == 350.0
    assert get_orders_value(db, ADMIN, status="CANCELLED", salesman_id=sumit.id) == 999.0
    assert get_orders_value(db, ADMIN, salesman_id=nitin.id) == 40.0
