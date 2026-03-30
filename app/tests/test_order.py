from datetime import date

import pytest

from app.services.order import create_outgoing_order, InsufficientStockError
from app.schemas.order import OrderCreate, OrderItemCreate
from app.core.security import create_access_token
from app.models import SKU, SKUBatch, Inventory, User, Brand
from app.models.enums import EmployeeRole

def test_fefo_allocation(db):
    brand = Brand(name="Brand")
    db.add(brand)
    db.commit()
    sku = SKU(name="Test SKU", brand_id=brand.id)
    db.add(sku)
    db.commit()
    batch_early = SKUBatch(
        sku_id=sku.id,
        warehouse_id=1,
        expiry_date=date(2024, 1, 1),
        quantity_received=5,
        remaining_quantity=5
    )
    batch_late = SKUBatch(
        sku_id=sku.id,
        warehouse_id=1,
        expiry_date=date(2024, 6, 1),
        quantity_received=10,
        remaining_quantity=10
    )
    db.add_all([batch_early, batch_late])
    db.commit()
    inventory = Inventory(sku_id=sku.id, warehouse_id=1, total_quantity=15)
    db.add(inventory)
    db.commit()
    user = User(email="admin@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add(user)
    db.commit()
    order = OrderCreate(retailer_id=1, items=[OrderItemCreate(sku_id=sku.id, quantity=6, unit_price=100, discount_amount=0)])
    result = create_outgoing_order(db, order, user)
    assert result is not None
    updated_inventory = db.query(Inventory).filter(Inventory.sku_id == sku.id).first()
    assert updated_inventory.total_quantity == 9
    refreshed_early = db.query(SKUBatch).filter(SKUBatch.id == batch_early.id).first()
    refreshed_late = db.query(SKUBatch).filter(SKUBatch.id == batch_late.id).first()
    assert refreshed_early.remaining_quantity == 0
    assert refreshed_late.remaining_quantity == 9

def test_insufficient_stock(db):
    brand = Brand(name="Brand")
    db.add(brand)
    db.commit()
    sku = SKU(name="Test SKU", brand_id=brand.id)
    db.add(sku)
    db.commit()
    batch = SKUBatch(sku_id=sku.id, warehouse_id=1, quantity_received=5, remaining_quantity=5)
    db.add(batch)
    db.commit()
    inventory = Inventory(sku_id=sku.id, warehouse_id=1, total_quantity=5)
    db.add(inventory)
    db.commit()
    user = User(email="admin2@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add(user)
    db.commit()
    order = OrderCreate(retailer_id=1, items=[OrderItemCreate(sku_id=sku.id, quantity=10, unit_price=100, discount_amount=0)])
    with pytest.raises(InsufficientStockError):
        create_outgoing_order(db, order, user)


def test_insufficient_stock_returns_409(client, db):
    brand = Brand(name="Brand")
    db.add(brand)
    db.flush()
    sku = SKU(name="Test SKU", brand_id=brand.id)
    db.add(sku)
    db.flush()
    batch = SKUBatch(sku_id=sku.id, warehouse_id=1, quantity_received=5, remaining_quantity=5)
    db.add(batch)
    inventory = Inventory(sku_id=sku.id, warehouse_id=1, total_quantity=5)
    db.add(inventory)
    admin = User(email="admin@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add(admin)
    db.commit()
    token = create_access_token({"user_id": admin.id, "role": EmployeeRole.ADMIN.value})

    response = client.post(
        "/orders",
        json={
            "retailer_id": 1,
            "warehouse_id": 1,
            "items": [{"sku_id": sku.id, "quantity": 10, "unit_price": 100, "discount_amount": 0}]
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409
