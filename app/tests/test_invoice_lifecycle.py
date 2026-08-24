"""Behavioural proof of D-01: an Invoice is minted at OUT_FOR_DELIVERY dispatch and
nowhere else — not at order creation, not at PENDING cancellation, not a second time on
DELIVERED, and never deleted by a post-dispatch cancellation.
"""
import itertools
import re

import pytest

from app.models import Brand, Employee, Inventory, Invoice, SKU, SKUBatch, Retailer, User, Warehouse
from app.models.enums import EmployeeRole
from app.schemas.order import OrderCreate, OrderItemCreate, StatusUpdate
from app.services.admin import get_orders_page
from app.services.order import create_outgoing_order, get_order_detail, update_order_status

_seed_counter = itertools.count()


def _seed_dispatchable_order(db, quantity=5):
    """Create a real Warehouse/Retailer/SKU/driver and a PENDING order against them —
    place_of_supply is NOT NULL on Invoice, so an order dispatched by these tests must
    resolve to a real retailer state, not a bare id."""
    n = next(_seed_counter)
    brand = Brand(name=f"Lifecycle Brand {n}")
    warehouse = Warehouse(name=f"Lifecycle WH {n}", location="Delhi", state="Delhi")
    retailer = Retailer(name=f"Lifecycle Retailer {n}", state="Delhi")
    db.add_all([brand, warehouse, retailer])
    db.commit()

    sku = SKU(name=f"Lifecycle SKU {n}", brand_id=brand.id)
    db.add(sku)
    db.commit()

    batch = SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=50, remaining_quantity=50)
    db.add(batch)
    inventory = Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=50)
    db.add(inventory)
    driver = Employee(name=f"Lifecycle Driver {n}", email=f"lifecycle-driver-{n}@ascend.com", role=EmployeeRole.DRIVER)
    user = User(email=f"lifecycle-admin-{n}@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add_all([driver, user])
    db.commit()

    order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer.id,
            warehouse_id=warehouse.id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=quantity, unit_price=100, discount_amount=0)],
        ),
        user,
    )
    return order, driver, user


def _dispatch(db, order, driver, current_user):
    order.delivery_driver_id = driver.id
    db.commit()
    update_order_status(db, order.id, StatusUpdate(status="READY_TO_SHIP"), current_user)
    update_order_status(db, order.id, StatusUpdate(status="OUT_FOR_DELIVERY"), current_user)
    db.refresh(order)


def test_order_creation_issues_no_invoice(db):
    order, _driver, _user = _seed_dispatchable_order(db)
    assert order.invoice is None
    assert order.invoice_number is None
    assert db.query(Invoice).count() == 0


def test_pending_cancellation_issues_no_invoice_and_burns_no_number(db):
    order, _driver, user = _seed_dispatchable_order(db)
    update_order_status(db, order.id, StatusUpdate(status="CANCELLED"), user)
    assert db.query(Invoice).count() == 0

    # A different order dispatches next. On the SQLite test engine the serial comes
    # from the max-plus-one fallback, so if the cancelled order above had consumed a
    # serial this would not be 1. (PostgreSQL sequence behaviour is covered by
    # test_invoice_sequence_pg.py.)
    other_order, other_driver, _other_user = _seed_dispatchable_order(db)
    _dispatch(db, other_order, other_driver, _other_user)
    assert other_order.invoice.invoice_serial == 1


def test_dispatch_issues_exactly_one_invoice(db):
    order, driver, user = _seed_dispatchable_order(db)
    _dispatch(db, order, driver, user)

    assert db.query(Invoice).count() == 1
    assert order.invoice is not None
    assert re.fullmatch(r"ASC\d{6}", order.invoice_number)
    assert order.invoice.order_id == order.id
    assert order.invoice.status == "ISSUED"
    assert len(order.invoice.lines) == len(order.items)


def test_ready_to_ship_alone_issues_no_invoice(db):
    order, driver, user = _seed_dispatchable_order(db)
    order.delivery_driver_id = driver.id
    db.commit()
    update_order_status(db, order.id, StatusUpdate(status="READY_TO_SHIP"), user)
    assert db.query(Invoice).count() == 0


def test_delivery_after_dispatch_does_not_issue_a_second_invoice(db):
    order, driver, user = _seed_dispatchable_order(db)
    _dispatch(db, order, driver, user)
    number_after_dispatch = order.invoice_number

    update_order_status(db, order.id, StatusUpdate(status="DELIVERED"), user)
    db.refresh(order)

    assert db.query(Invoice).count() == 1
    assert order.invoice_number == number_after_dispatch


def test_cancellation_after_dispatch_leaves_the_invoice_intact(db):
    order, driver, user = _seed_dispatchable_order(db)
    _dispatch(db, order, driver, user)
    number_before_cancel = order.invoice_number

    update_order_status(db, order.id, StatusUpdate(status="CANCELLED"), user)  # must not raise
    db.refresh(order)

    assert order.invoice is not None
    assert order.invoice_number == number_before_cancel


def test_serialize_order_reports_null_then_the_number(db):
    order, driver, user = _seed_dispatchable_order(db)
    detail_before = get_order_detail(db, order.id, user)
    assert detail_before["invoice_number"] is None

    _dispatch(db, order, driver, user)
    detail_after = get_order_detail(db, order.id, user)
    assert detail_after["invoice_number"] == order.invoice_number


def test_invoice_number_property_is_read_only(db):
    order, _driver, _user = _seed_dispatchable_order(db)
    with pytest.raises(AttributeError):
        order.invoice_number = "HACKED"


def test_admin_has_invoice_filter_splits_pending_from_dispatched(db):
    pending_order, _pending_driver, pending_user = _seed_dispatchable_order(db)
    dispatched_order, dispatched_driver, _dispatched_user = _seed_dispatchable_order(db)
    _dispatch(db, dispatched_order, dispatched_driver, _dispatched_user)

    with_invoice, _total = get_orders_page(db, pending_user, has_invoice=True)
    assert [o.id for o in with_invoice] == [dispatched_order.id]

    without_invoice, _total = get_orders_page(db, pending_user, has_invoice=False)
    assert [o.id for o in without_invoice] == [pending_order.id]

    searched, _total = get_orders_page(db, pending_user, search=dispatched_order.invoice_number)
    assert [o.id for o in searched] == [dispatched_order.id]
