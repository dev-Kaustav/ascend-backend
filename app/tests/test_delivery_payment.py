"""Marking an order delivered when it was already paid before delivery (e.g. salesman cash
collection) must not be refused as a zero-rupee payment."""
import pytest

from app.models import Account
from app.models.enums import EmployeeRole, OrderStatus, PaymentStatus
from app.schemas.order import StatusUpdate
from app.services.finance import calculate_order_outstanding
from app.services.order import update_order_status
from app.tests.test_order_transitions import _seed_dispatchable_order, _user, _walk_order_to


def _out_for_delivery(db, prepaid=False):
    order, driver, _ = _seed_dispatchable_order(db)
    wm = _user(db, EmployeeRole.WAREHOUSE_MANAGER)
    drv = _user(db, EmployeeRole.DRIVER, employee_id=driver.id)
    _walk_order_to(db, order, driver, OrderStatus.OUT_FOR_DELIVERY, wm, drv)
    db.refresh(order)
    if prepaid:
        db.add(Account(order_id=order.id, amount=calculate_order_outstanding(order), transaction_reference=f"SALESMAN-CASH-t{order.id}"))
        db.commit()
        db.refresh(order)
    return order, drv


def test_prepaid_order_marked_paid_delivers_without_a_second_payment(db):
    order, drv = _out_for_delivery(db, prepaid=True)
    update_order_status(db, order.id, StatusUpdate(status="DELIVERED", payment_status="PAID", payment_mode="CASH"), drv)
    db.refresh(order)
    assert order.status == OrderStatus.DELIVERED
    assert order.payment_status == PaymentStatus.PAID
    assert len(order.payments) == 1


def test_prepaid_order_refuses_an_extra_amount(db):
    order, drv = _out_for_delivery(db, prepaid=True)
    with pytest.raises(ValueError, match="already fully paid"):
        update_order_status(db, order.id, StatusUpdate(status="DELIVERED", payment_status="PAID", payment_mode="CASH", payment_amount=500), drv)


def test_unpaid_order_still_records_cash_on_delivery(db):
    order, drv = _out_for_delivery(db)
    update_order_status(db, order.id, StatusUpdate(status="DELIVERED", payment_status="PAID", payment_mode="CASH"), drv)
    db.refresh(order)
    assert order.payment_status == PaymentStatus.PAID
    assert calculate_order_outstanding(order) == 0
