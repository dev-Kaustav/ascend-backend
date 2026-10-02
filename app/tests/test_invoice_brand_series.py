"""Per-brand, per-channel invoice series with five-digit consecutive serials."""
import itertools

import pytest

from app.models import Brand, Employee, Inventory, Invoice, SKU, SKUBatch, Retailer, User, Warehouse
from app.models.enums import EmployeeRole
from app.schemas.order import OrderCreate, OrderItemCreate, StatusUpdate
from app.services.invoice import (
    MultiBrandInvoiceError,
    brand_series_code,
    issue_invoice_for_order,
    next_invoice_number,
)
from app.services.order import create_outgoing_order, update_order_status

_seed_counter = itertools.count()


def _seed_brand(db, brand_name):
    n = next(_seed_counter)
    brand = Brand(name=brand_name)
    warehouse = Warehouse(name=f"Series WH {n}", location="Delhi", state="Delhi")
    retailer = Retailer(name=f"Series Retailer {n}", state="Delhi")
    db.add_all([brand, warehouse, retailer])
    db.commit()

    sku = SKU(name=f"Series SKU {n}", brand_id=brand.id, hsn_code="12345678",
              mrp=150, sgst_percent=9, cgst_percent=9, igst_percent=18)
    db.add(sku)
    db.commit()

    db.add(SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=100, remaining_quantity=100))
    db.add(Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=100))
    driver = Employee(name=f"Series Driver {n}", email=f"ser-driver-{n}@ascend.com", role=EmployeeRole.DRIVER)
    user = User(email=f"ser-admin-{n}@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add_all([driver, user])
    db.commit()
    return warehouse, retailer, sku, driver, user


def _dispatch_order(db, warehouse, retailer, skus, driver, user, quantity=2, dispatcher=None):
    order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer.id,
            warehouse_id=warehouse.id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=quantity, unit_price=100, discount_amount=0)
                   for sku in skus],
        ),
        user,
    )
    order.delivery_driver_id = driver.id
    db.commit()
    update_order_status(db, order.id, StatusUpdate(status="READY_TO_SHIP"), dispatcher or user)
    update_order_status(db, order.id, StatusUpdate(status="OUT_FOR_DELIVERY"), dispatcher or user)
    db.refresh(order)
    return order


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Jabsons", "JAB"),
        ("jabsons foods", "JAB"),
        ("  Jab-Sons  ", "JAB"),
        ("3M", "3M"),
        ("K", "K"),
    ],
)
def test_brand_series_code_is_first_three_alphanumerics_uppercased(name, expected):
    assert brand_series_code(name) == expected


@pytest.mark.parametrize("name", ["", "   ", "---", None])
def test_brand_series_code_rejects_names_with_no_alphanumerics(name):
    with pytest.raises(ValueError):
        brand_series_code(name)


def test_invoice_number_carries_the_brand_code(db):
    warehouse, retailer, sku, driver, user = _seed_brand(db, "Jabsons Foods")
    order = _dispatch_order(db, warehouse, retailer, [sku], driver, user)

    assert order.invoice.invoice_number == "ASC/JAB/OF/00001"
    assert order.invoice.invoice_series == "JAB/OF"
    assert order.invoice.invoice_serial == 1


def test_serial_is_consecutive_within_a_brand(db):
    warehouse, retailer, sku, driver, user = _seed_brand(db, "Jabsons")
    numbers = [
        _dispatch_order(db, warehouse, retailer, [sku], driver, user).invoice.invoice_number
        for _ in range(3)
    ]
    assert numbers == ["ASC/JAB/OF/00001", "ASC/JAB/OF/00002", "ASC/JAB/OF/00003"]


def test_each_brand_counts_from_one_independently(db):
    wh_a, ret_a, sku_a, drv_a, user_a = _seed_brand(db, "Jabsons")
    wh_b, ret_b, sku_b, drv_b, user_b = _seed_brand(db, "Kalmi")

    first_a = _dispatch_order(db, wh_a, ret_a, [sku_a], drv_a, user_a)
    first_b = _dispatch_order(db, wh_b, ret_b, [sku_b], drv_b, user_b)
    second_a = _dispatch_order(db, wh_a, ret_a, [sku_a], drv_a, user_a)

    assert first_a.invoice.invoice_number == "ASC/JAB/OF/00001"
    assert first_b.invoice.invoice_number == "ASC/KAL/OF/00001"
    assert second_a.invoice.invoice_number == "ASC/JAB/OF/00002"


def test_brands_sharing_a_code_share_a_series_rather_than_colliding(db):
    """"Jabsons" and "Jabra" both derive JAB. The counter is keyed on the code, so the
    second one continues the series instead of reissuing 0001."""
    wh_a, ret_a, sku_a, drv_a, user_a = _seed_brand(db, "Jabsons")
    wh_b, ret_b, sku_b, drv_b, user_b = _seed_brand(db, "Jabra")

    first = _dispatch_order(db, wh_a, ret_a, [sku_a], drv_a, user_a)
    second = _dispatch_order(db, wh_b, ret_b, [sku_b], drv_b, user_b)

    assert first.invoice.invoice_number == "ASC/JAB/OF/00001"
    assert second.invoice.invoice_number == "ASC/JAB/OF/00002"


def test_mixed_brand_order_is_refused_rather_than_mislabelled(db):
    warehouse, retailer, sku_a, driver, user = _seed_brand(db, "Jabsons")
    other_brand = Brand(name="Kalmi")
    db.add(other_brand)
    db.commit()
    sku_b = SKU(name="Mixed SKU", brand_id=other_brand.id, hsn_code="12345678",
                mrp=150, sgst_percent=9, cgst_percent=9, igst_percent=18)
    db.add(sku_b)
    db.commit()
    db.add(SKUBatch(sku_id=sku_b.id, warehouse_id=warehouse.id, quantity_received=100, remaining_quantity=100))
    db.add(Inventory(sku_id=sku_b.id, warehouse_id=warehouse.id, total_quantity=100))
    db.commit()

    order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer.id,
            warehouse_id=warehouse.id,
            items=[
                OrderItemCreate(sku_id=sku_a.id, quantity=2, unit_price=100, discount_amount=0),
                OrderItemCreate(sku_id=sku_b.id, quantity=2, unit_price=100, discount_amount=0),
            ],
        ),
        user,
    )
    db.commit()

    with pytest.raises(MultiBrandInvoiceError, match="spans 2 brands"):
        issue_invoice_for_order(db, order)

    assert db.query(Invoice).count() == 0


def test_externally_numbered_import_consumes_no_serial(db):
    """The historical-import path assigns its own number; it must not advance a series."""
    warehouse, retailer, sku, driver, user = _seed_brand(db, "Jabsons")
    order = _dispatch_order(db, warehouse, retailer, [sku], driver, user)
    assert order.invoice.invoice_number == "ASC/JAB/OF/00001"

    # Next allocation in the series is 2 — the import below must leave it there.
    assert next_invoice_number(db, "JAB/OF") == ("ASC/JAB/OF/00002", 2)
    assert next_invoice_number(db, "JAB/OF") == ("ASC/JAB/OF/00003", 3)


def test_online_and_offline_have_independent_five_digit_series(db):
    from app.tests.store_helpers import ready_retailer

    warehouse, retailer, sku, driver, admin = _seed_brand(db, "Jabsons")
    retailer_user, online_retailer, _ = ready_retailer(db)
    offline = _dispatch_order(db, warehouse, retailer, [sku], driver, admin)
    online = _dispatch_order(db, warehouse, online_retailer, [sku], driver, retailer_user, dispatcher=admin)
    second_offline = _dispatch_order(db, warehouse, retailer, [sku], driver, admin)

    assert offline.channel == "OFFLINE"
    assert online.channel == "ONLINE"
    assert offline.invoice.invoice_number == "ASC/JAB/OF/00001"
    assert online.invoice.invoice_number == "ASC/JAB/ON/00001"
    assert online.invoice.invoice_series == "JAB/ON"
    assert len(online.invoice.invoice_number) == 16
    assert second_offline.invoice.invoice_number == "ASC/JAB/OF/00002"
    assert issue_invoice_for_order(db, online).invoice_number == "ASC/JAB/ON/00001"


def test_existing_invoice_number_is_preserved_and_consumes_no_channel_serial(db):
    warehouse, retailer, sku, _, admin = _seed_brand(db, "Jabsons")
    order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer.id,
            warehouse_id=warehouse.id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=2, unit_price=100)],
        ),
        admin,
    )
    historical = issue_invoice_for_order(db, order, invoice_number="ASC/JAB/0042")
    db.commit()

    assert issue_invoice_for_order(db, order).invoice_number == "ASC/JAB/0042"
    assert historical.invoice_serial is None
    assert next_invoice_number(db, "JAB/OF") == ("ASC/JAB/OF/00001", 1)
