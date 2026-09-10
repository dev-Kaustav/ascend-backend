from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.services.order import create_outgoing_order, update_order_status, InsufficientStockError
from app.services import inventory as inventory_service
from app.schemas.order import OrderCreate, OrderItemCreate, StatusUpdate
from app.core.security import create_access_token
from app.models import SKU, SKUBatch, Inventory, Order, User, Brand, Retailer, Warehouse
from app.models.enums import EmployeeRole

def test_fefo_allocation(db):
    brand = Brand(name="Brand")
    warehouse = Warehouse(name="FEFO WH", location="Delhi", state="Delhi")
    retailer = Retailer(name="FEFO Retailer", state="Delhi")
    db.add_all([brand, warehouse, retailer])
    db.commit()
    sku = SKU(name="Test SKU", brand_id=brand.id)
    db.add(sku)
    db.commit()
    # D1: dates derived from the injected seam, never pinned to an absolute
    # calendar date — an absolute past date here would become expired,
    # unallocatable stock and silently invert what this test claims to prove.
    today = inventory_service.current_business_date()
    batch_early = SKUBatch(
        sku_id=sku.id,
        warehouse_id=warehouse.id,
        expiry_date=today + timedelta(days=30),
        quantity_received=5,
        remaining_quantity=5
    )
    batch_late = SKUBatch(
        sku_id=sku.id,
        warehouse_id=warehouse.id,
        expiry_date=today + timedelta(days=90),
        quantity_received=10,
        remaining_quantity=10
    )
    db.add_all([batch_early, batch_late])
    db.commit()
    inventory = Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=15)
    db.add(inventory)
    db.commit()
    user = User(email="admin@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add(user)
    db.commit()
    order = OrderCreate(
        retailer_id=retailer.id,
        warehouse_id=warehouse.id,
        items=[OrderItemCreate(sku_id=sku.id, quantity=6, unit_price=100, discount_amount=0)],
    )
    result = create_outgoing_order(db, order, user)
    assert result is not None
    updated_inventory = db.query(Inventory).filter(Inventory.sku_id == sku.id).first()
    assert updated_inventory.total_quantity == 15
    assert updated_inventory.reserved_quantity == 6
    refreshed_early = db.query(SKUBatch).filter(SKUBatch.id == batch_early.id).first()
    refreshed_late = db.query(SKUBatch).filter(SKUBatch.id == batch_late.id).first()
    assert refreshed_early.remaining_quantity == 5
    assert refreshed_early.reserved_quantity == 5
    assert refreshed_late.remaining_quantity == 10
    assert refreshed_late.reserved_quantity == 1

    result.delivery_driver_id = 1
    db.commit()
    update_order_status(db, result.id, StatusUpdate(status="READY_TO_SHIP"), user)
    update_order_status(db, result.id, StatusUpdate(status="OUT_FOR_DELIVERY"), user)
    assert updated_inventory.total_quantity == 9
    assert updated_inventory.reserved_quantity == 0
    assert refreshed_early.remaining_quantity == 0
    assert refreshed_late.remaining_quantity == 9

def test_insufficient_stock(db):
    brand = Brand(name="Brand")
    warehouse = Warehouse(name="Short WH", location="Delhi", state="Delhi")
    db.add_all([brand, warehouse])
    db.commit()
    sku = SKU(name="Test SKU", brand_id=brand.id)
    db.add(sku)
    db.commit()
    batch = SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=5, remaining_quantity=5)
    db.add(batch)
    db.commit()
    inventory = Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=5)
    db.add(inventory)
    db.commit()
    user = User(email="admin2@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add(user)
    db.commit()
    order = OrderCreate(retailer_id=1, warehouse_id=warehouse.id, items=[OrderItemCreate(sku_id=sku.id, quantity=10, unit_price=100, discount_amount=0)])
    with pytest.raises(InsufficientStockError) as excinfo:
        create_outgoing_order(db, order, user)
    # INVT-07: the refusal has to be actionable — which SKU, which warehouse, how short.
    message = str(excinfo.value)
    assert "Test SKU" in message
    assert "Short WH" in message
    assert "requested 10" in message
    assert "available 5" in message


def test_insufficient_stock_returns_409(client, db):
    brand = Brand(name="Brand")
    warehouse = Warehouse(name="409 WH", location="Delhi", state="Delhi")
    db.add_all([brand, warehouse])
    db.flush()
    sku = SKU(name="Test SKU", brand_id=brand.id)
    db.add(sku)
    db.flush()
    batch = SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=5, remaining_quantity=5)
    db.add(batch)
    inventory = Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=5)
    db.add(inventory)
    admin = User(email="admin@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add(admin)
    db.commit()
    token = create_access_token({"user_id": admin.id, "role": EmployeeRole.ADMIN.value})

    response = client.post(
        "/orders",
        json={
            "retailer_id": 1,
            "warehouse_id": warehouse.id,
            "items": [{"sku_id": sku.id, "quantity": 10, "unit_price": 100, "discount_amount": 0}]
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409
    # A string detail, not a list of objects: the order form renders it directly.
    assert "409 WH" in response.json()["detail"]


def test_order_without_warehouse_is_rejected(client, db):
    """INVT-07: no silent fallback to warehouse 1. The order that prompted this booked
    against a warehouse nobody chose and came back as 'Insufficient stock'."""
    brand = Brand(name="Brand")
    warehouse = Warehouse(name="Chosen WH", location="Delhi", state="Delhi")
    db.add_all([brand, warehouse])
    db.flush()
    sku = SKU(name="Test SKU", brand_id=brand.id)
    db.add(sku)
    db.flush()
    db.add(SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=50, remaining_quantity=50))
    db.add(Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=50))
    admin = User(email="admin-nowh@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add(admin)
    db.commit()
    token = create_access_token({"user_id": admin.id, "role": EmployeeRole.ADMIN.value})

    response = client.post(
        "/orders",
        json={
            "retailer_id": 1,
            "items": [{"sku_id": sku.id, "quantity": 10, "unit_price": 100, "discount_amount": 0}]
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Select a warehouse for this order."
    assert db.query(Order).count() == 0


def test_order_against_unknown_warehouse_is_rejected(db):
    brand = Brand(name="Brand")
    db.add(brand)
    db.commit()
    sku = SKU(name="Test SKU", brand_id=brand.id)
    db.add(sku)
    user = User(email="admin-badwh@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add(user)
    db.commit()
    order = OrderCreate(retailer_id=1, warehouse_id=9999, items=[OrderItemCreate(sku_id=sku.id, quantity=1, unit_price=100, discount_amount=0)])
    with pytest.raises(ValueError, match="Warehouse not found."):
        create_outgoing_order(db, order, user)


def test_stock_in_another_warehouse_names_the_warehouse_ordered_against(db):
    """The reported incident: stock received into warehouse B, order placed against A."""
    brand = Brand(name="Brand")
    warehouse_a = Warehouse(name="WH A", location="Delhi", state="Delhi")
    warehouse_b = Warehouse(name="WH B", location="Delhi", state="Delhi")
    db.add_all([brand, warehouse_a, warehouse_b])
    db.commit()
    sku = SKU(name="Roasted Peanuts", brand_id=brand.id)
    db.add(sku)
    db.commit()
    db.add(SKUBatch(sku_id=sku.id, warehouse_id=warehouse_b.id, quantity_received=100, remaining_quantity=100))
    db.add(Inventory(sku_id=sku.id, warehouse_id=warehouse_b.id, total_quantity=100))
    user = User(email="admin-crosswh@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add(user)
    db.commit()

    order = OrderCreate(retailer_id=1, warehouse_id=warehouse_a.id, items=[OrderItemCreate(sku_id=sku.id, quantity=36, unit_price=10, discount_amount=0)])
    with pytest.raises(InsufficientStockError) as excinfo:
        create_outgoing_order(db, order, user)
    message = str(excinfo.value)
    assert "WH A" in message
    assert "WH B" not in message
    assert "available 0" in message


def test_order_taxes_use_warehouse_and_retailer_state(db):
    brand = Brand(name="Brand")
    warehouse = Warehouse(name="Delhi WH", location="Delhi", state="Delhi")
    retailer_same = Retailer(name="Same State", state="Delhi")
    retailer_other = Retailer(name="Other State", state="Karnataka")
    user = User(email="admin-tax@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add_all([brand, warehouse, retailer_same, retailer_other, user])
    db.flush()
    sku = SKU(name="Tax SKU", brand_id=brand.id, mrp=100, sgst_percent=2.5, cgst_percent=2.5, igst_percent=5)
    db.add(sku)
    db.flush()
    db.add_all([
        SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=10, remaining_quantity=10),
        Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=10),
    ])
    db.commit()

    same_order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer_same.id,
            warehouse_id=warehouse.id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=1, unit_price=100, discount_amount=0)],
        ),
        user,
    )
    assert sorted(tax.tax_type for tax in same_order.items[0].taxes) == ["CGST", "SGST"]

    other_order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer_other.id,
            warehouse_id=warehouse.id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=1, unit_price=100, discount_amount=0)],
        ),
        user,
    )
    assert [tax.tax_type for tax in other_order.items[0].taxes] == ["IGST"]


def test_order_accepts_client_supplied_tax_rates(db):
    """The frontend posts taxes=[{tax_type: "GST", rate: <float>}] for every line whose SKU
    carries GST (components/orders/AdminOrderCreate.jsx). Those floats reach
    _fallback_tax_rate, which sums onto a Decimal start value — a TypeError no caller catches,
    so every such order returned 500. The tests above all omit `taxes`, which is why the suite
    stayed green while order creation was broken in production."""
    brand = Brand(name="Client Tax Brand")
    warehouse = Warehouse(name="Client WH", location="Delhi", state="Delhi")
    retailer = Retailer(name="Client Retailer", state="Delhi")
    user = User(email="admin-client-tax@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add_all([brand, warehouse, retailer, user])
    db.flush()
    sku = SKU(name="Client Tax SKU", brand_id=brand.id, mrp=60, sgst_percent=2.5, cgst_percent=2.5)
    db.add(sku)
    db.flush()
    db.add_all([
        SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=10, remaining_quantity=10),
        Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=10),
    ])
    db.commit()

    order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer.id,
            warehouse_id=warehouse.id,
            items=[
                OrderItemCreate(
                    sku_id=sku.id,
                    quantity=5,
                    unit_price=48.0,
                    discount_amount=12.0,
                    taxes=[{"tax_type": "GST", "rate": 5.0}],
                )
            ],
        ),
        user,
    )
    assert sorted(tax.tax_type for tax in order.items[0].taxes) == ["CGST", "SGST"]


def test_client_tax_rates_split_when_sku_has_no_gst_percentages(db):
    """With no SGST/CGST on the SKU, the client-supplied rate is the only source, so it must
    still parse as a number and split in half rather than raising."""
    brand = Brand(name="Fallback Brand")
    warehouse = Warehouse(name="Fallback WH", location="Delhi", state="Delhi")
    retailer = Retailer(name="Fallback Retailer", state="Delhi")
    user = User(email="admin-fallback-tax@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add_all([brand, warehouse, retailer, user])
    db.flush()
    sku = SKU(name="No GST SKU", brand_id=brand.id, mrp=60)
    db.add(sku)
    db.flush()
    db.add_all([
        SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=10, remaining_quantity=10),
        Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=10),
    ])
    db.commit()

    order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer.id,
            warehouse_id=warehouse.id,
            items=[
                OrderItemCreate(
                    sku_id=sku.id,
                    quantity=1,
                    unit_price=48.0,
                    taxes=[{"tax_type": "GST", "rate": 5.0}],
                )
            ],
        ),
        user,
    )
    rates = {tax.tax_type: tax.rate for tax in order.items[0].taxes}
    assert sorted(rates) == ["CGST", "SGST"]
    assert sum(rates.values()) == Decimal("5.00")


def test_client_supplied_order_date_cannot_backdate_an_order(client, db):
    """A booked order is dated by the server clock.

    order_date used to be an OrderCreate field that overwrote created_at, so any caller could
    file an order into last month — the same shape of problem as inventing a retailer, since it
    rewrites history with nothing to check it against. The field is gone; this pins that sending
    it anyway is ignored rather than honoured, and that an old client doing so is not rejected.
    """
    from app.core.security import create_access_token, get_password_hash

    brand = Brand(name="Backdate Brand")
    warehouse = Warehouse(name="Backdate WH", location="Delhi", state="Delhi")
    retailer = Retailer(name="Backdate Retailer", state="Delhi")
    db.add_all([brand, warehouse, retailer])
    db.commit()
    sku = SKU(name="Backdate SKU", brand_id=brand.id, mrp=100)
    db.add(sku)
    db.commit()

    today = inventory_service.current_business_date()
    db.add(
        SKUBatch(
            sku_id=sku.id,
            warehouse_id=warehouse.id,
            expiry_date=today + timedelta(days=90),
            quantity_received=10,
            remaining_quantity=10,
        )
    )
    db.add(Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=10))
    admin = User(email="backdate-admin@ascend.com", password_hash=get_password_hash("x"), role=EmployeeRole.ADMIN)
    db.add(admin)
    db.commit()

    headers = {"Authorization": f"Bearer {create_access_token({'user_id': admin.id, 'role': 'ADMIN'})}"}
    response = client.post(
        "/orders",
        json={
            "retailer_id": retailer.id,
            "warehouse_id": warehouse.id,
            "order_date": "2020-01-01",
            "items": [{"sku_id": sku.id, "quantity": 1, "unit_price": 100, "discount_amount": 0}],
        },
        headers=headers,
    )

    # Accepted, not rejected: the extra key is ignored so an older client keeps working.
    assert response.status_code == 200
    created = db.query(Order).filter(Order.id == response.json()["id"]).first()
    assert created.created_at.year != 2020
    assert created.created_at.date() == datetime.now(created.created_at.tzinfo).date()
