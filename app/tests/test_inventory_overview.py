"""Current stock includes the catalogue; period filters affect only movement flows."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.core.security import create_access_token, get_password_hash
from app.models import Brand, Inventory, InventoryTransaction, SKU, SKUBatch, User, Warehouse
from app.models.enums import EmployeeRole, TransactionType
from app.services.metrics import stock_overview, stock_value


def _seed(db):
    brand = Brand(name="Stock overview brand")
    city, dlf = Warehouse(name="City", state="Delhi"), Warehouse(name="DLF", state="Delhi")
    db.add_all([brand, city, dlf])
    db.flush()
    stocked = SKU(name="Peanuts", brand_id=brand.id, distributor_landing_price=Decimal("10"))
    empty = SKU(name="Never stocked", brand_id=brand.id, distributor_landing_price=Decimal("4"))
    db.add_all([stocked, empty])
    db.flush()
    db.add_all([
        Inventory(sku_id=stocked.id, warehouse_id=city.id, total_quantity=40, reserved_quantity=5),
        Inventory(sku_id=stocked.id, warehouse_id=dlf.id, total_quantity=20, reserved_quantity=0),
        SKUBatch(sku_id=stocked.id, warehouse_id=city.id, remaining_quantity=30, quantity_received=30, reserved_quantity=5, expiry_date=date.today()+timedelta(days=30)),
        SKUBatch(sku_id=stocked.id, warehouse_id=city.id, remaining_quantity=10, quantity_received=10, reserved_quantity=0, expiry_date=date.today()-timedelta(days=1)),
        SKUBatch(sku_id=stocked.id, warehouse_id=dlf.id, remaining_quantity=20, quantity_received=20, reserved_quantity=0),
        InventoryTransaction(sku_id=stocked.id, warehouse_id=city.id, transaction_type=TransactionType.IN, quantity=100, created_at=datetime(2026, 10, 1, 6, tzinfo=timezone.utc)),
        InventoryTransaction(sku_id=stocked.id, warehouse_id=city.id, transaction_type=TransactionType.OUT, quantity=60, created_at=datetime(2026, 10, 5, 6, tzinfo=timezone.utc)),
    ])
    db.commit()
    return city, dlf, stocked, empty


def test_includes_never_stocked_and_uses_allocator_availability(db):
    city, _, stocked, empty = _seed(db)
    result = stock_overview(db, warehouse_id=city.id, from_date="2026-10-01", to_date="2026-10-10")
    assert result["counts"] == {"all": 2, "running_low": 1, "out_of_stock": 1, "expiring": 1}
    assert result["skus"][0]["sku_id"] == empty.id
    row = next(row for row in result["skus"] if row["sku_id"] == stocked.id)
    assert (row["on_hand"], row["available"], row["reserved"], row["expired"]) == (40, 25, 5, 10)
    assert row["days_left"] == 4.2
    assert [batch["remaining"] for batch in row["batches"]] == [10, 30]
    assert row["value"] == 400
    assert result["totals"]["dispatched"] == 60
    assert result["last_movement_at"].date() == date(2026, 10, 5)


def test_period_and_warehouse_scope_flows_but_never_hide_current_stock(db):
    _, dlf, stocked, _ = _seed(db)
    later = stock_overview(db, from_date="2026-11-01", to_date="2026-11-30")
    assert later["totals"]["on_hand"] == 60
    assert later["totals"]["value"] == 600
    assert later["totals"]["received"] == later["totals"]["dispatched"] == 0
    assert all(row["days_left"] is None for row in later["skus"])
    other = stock_overview(db, warehouse_id=dlf.id)
    assert other["totals"]["on_hand"] == 20
    assert other["last_movement_at"] is None
    assert other["counts"]["expiring"] == 0
    assert next(row for row in other["skus"] if row["sku_id"] == stocked.id)["batches"][0]["expiry_date"] is None


def test_overview_endpoint_is_available_to_warehouse_staff(client, db):
    city, _, _, _ = _seed(db)
    user = User(email="stock-overview@test.invalid", password_hash=get_password_hash("password"), role=EmployeeRole.WAREHOUSE_MANAGER)
    db.add(user)
    db.commit()
    headers = {"Authorization": f"Bearer {create_access_token({'user_id': user.id, 'role': 'WAREHOUSE_MANAGER'})}"}
    result = client.get(f"/admin/inventory/overview?warehouse_id={city.id}", headers=headers)
    assert result.status_code == 200
    assert result.json()["totals"]["available"] == 25


def test_stock_value_matches_the_displayed_sku_values_to_the_penny(db):
    brand = Brand(name="Penny rounding")
    warehouse = Warehouse(name="Penny warehouse", state="Delhi")
    db.add_all([brand, warehouse])
    db.flush()
    for name in ("One", "Two"):
        sku = SKU(name=name, brand_id=brand.id, distributor_landing_price=Decimal("0.335"))
        db.add(sku)
        db.flush()
        db.add(Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=1, reserved_quantity=0))
    db.commit()
    overview = stock_overview(db)
    assert [row["value"] for row in overview["skus"]] == [0.34, 0.34]
    assert overview["totals"]["value"] == stock_value(db)["inventory_value"] == 0.68
