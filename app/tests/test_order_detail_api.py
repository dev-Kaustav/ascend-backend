"""GET /orders/{id} returns the order's history (the trail), its beat and each line's SKU code.

The serializer always built these, but the response model dropped them, so the order screens'
history timeline was always empty.
"""
from app.models import SKU, Beat
from app.models.enums import EmployeeRole
from app.schemas.order import StatusUpdate
from app.services.order import update_order_status
from app.tests.store_helpers import auth_headers, make_user
from app.tests.test_order_transitions import _seed_dispatchable_order


def test_order_detail_returns_the_trail_in_order_with_names(client, db):
    order, driver, _seed_user = _seed_dispatchable_order(db)
    wm = make_user(db, EmployeeRole.WAREHOUSE_MANAGER)
    update_order_status(db, order.id, StatusUpdate(status="READY_TO_SHIP"), wm)
    update_order_status(db, order.id, StatusUpdate(status="OUT_FOR_DELIVERY", delivery_driver_id=driver.id), wm)

    admin = make_user(db, EmployeeRole.ADMIN)
    response = client.get(f"/orders/{order.id}", headers=auth_headers(admin))

    assert response.status_code == 200, response.text
    trails = response.json()["trails"]
    assert [t["order_status"] for t in trails] == ["PENDING", "READY_TO_SHIP", "OUT_FOR_DELIVERY"]
    assert [t["description"] for t in trails] == ["Order created", "Order accepted", f"Sent out with {driver.name}"]
    assert all(t["created_at"] for t in trails)


def test_order_detail_returns_the_beat_and_sku_codes(client, db):
    order, _driver, _seed_user = _seed_dispatchable_order(db)
    beat = Beat(name="DLF Phase 1")
    db.add(beat)
    db.commit()
    order.beat_id = beat.id
    db.query(SKU).filter(SKU.id == order.items[0].sku_id).one().code = "PNT-CS-27g"
    db.commit()

    admin = make_user(db, EmployeeRole.ADMIN)
    body = client.get(f"/orders/{order.id}", headers=auth_headers(admin)).json()

    assert body["beat_name"] == "DLF Phase 1"
    assert body["items"][0]["sku_code"] == "PNT-CS-27g"


def test_order_detail_returns_pack_lines_and_the_bill_totals(client, db):
    # The order screens read lines as packs and the bill from these server figures; a field the
    # response model does not declare is silently dropped, so this goes through the API.
    order, _driver, _seed_user = _seed_dispatchable_order(db, quantity=10)
    sku = db.query(SKU).filter(SKU.id == order.items[0].sku_id).one()
    sku.pack_type, sku.units_per_pack = "ladi", 5
    order.items[0].discount_amount = 50
    db.commit()

    admin = make_user(db, EmployeeRole.ADMIN)
    body = client.get(f"/orders/{order.id}", headers=auth_headers(admin)).json()

    line = body["items"][0]
    assert (line["pack_count"], line["pack_price"], line["line_total"]) == (2, 475.0, 950.0)
    assert (body["mrp_total"], body["discount_total"]) == (1000.0, 50.0)
