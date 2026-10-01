"""Store checkout (08-09): a ready retailer places a server-priced order through the shared
order pipeline, to a saved address, and can read it back (STORE-06, STORE-12, PORT-06)."""
from decimal import Decimal

from app.models import Employee, Inventory, Order, OrderItem, SKUBatch
from app.models.enums import EmployeeRole, OrderStatus
from app.schemas.order import OrderCreate, OrderItemCreate
from app.services.finance import calculate_order_totals
from app.services.order import create_outgoing_order
from app.tests.store_helpers import (
    auth_headers,
    make_brand,
    make_category,
    make_storefront,
    make_user,
    ready_retailer,
    stocked_sku,
)


def _store(db, *, qty=20, mrp="22.5", amount="18.0", gst_rate="12", warehouse_state="Delhi"):
    warehouse = make_storefront(db, state=warehouse_state)
    brand = make_brand(db)
    category = make_category(db, "Chips")
    sku = stocked_sku(
        db,
        warehouse=warehouse,
        brand=brand,
        category=category,
        name="Masala Chips",
        code="CHN-MSL-23g",
        mrp=mrp,
        amount=amount,
        gst_rate=Decimal(gst_rate),
        qty=qty,
    )
    return warehouse, brand, category, sku


def _order_body(sku, address, quantity=3, **extra):
    return {"items": [{"sku_id": sku.id, "quantity": quantity}], "address_id": address.id, **extra}


def test_ready_retailer_places_a_server_priced_order_to_a_saved_address(client, db):
    warehouse, _, _, sku = _store(db)
    user, retailer, address = ready_retailer(db)
    other_user, other_retailer, _ = ready_retailer(db)

    body = {
        "items": [
            {
                "sku_id": sku.id,
                "quantity": 3,
                # Every one of these is forged and must be ignored (criterion 3, T-08-26).
                "unit_price": 1,
                "discount_amount": 999,
                "taxes": [],
                "retailer_id": other_retailer.id,
            }
        ],
        "address_id": address.id,
        "warehouse_id": 999,
        "retailer_id": other_retailer.id,
        "salesman_id": 12345,
    }
    response = client.post("/store/orders", json=body, headers=auth_headers(user))
    assert response.status_code == 201, response.text

    order = db.query(Order).one()
    assert order.to_entity_type == "RETAILER" and order.to_entity_id == retailer.id
    assert order.from_entity_type == "WAREHOUSE" and order.from_entity_id == warehouse.id
    assert order.status == OrderStatus.PENDING
    assert order.salesman_id is None

    (item,) = db.query(OrderItem).all()
    assert item.unit_price == Decimal("22.50")
    assert item.discount_amount == Decimal("13.50")  # (22.5 - 18.0) x 3
    assert item.quantity == 3
    assert db.query(SKUBatch).one().reserved_quantity == 3
    assert db.query(Inventory).one().reserved_quantity == 3

    # The ship-to is copied from the chosen saved address (D-29).
    assert order.delivery_address_id == address.id
    assert order.ship_to_label == address.label
    assert order.ship_to_line1 == address.line1
    assert order.ship_to_line2 == address.line2
    assert order.ship_to_landmark == address.landmark
    assert order.ship_to_city == address.city
    assert order.ship_to_state == address.state
    assert order.ship_to_pincode == address.pincode
    assert order.ship_to_latitude == address.latitude
    assert order.ship_to_longitude == address.longitude

    body = response.json()
    assert body["id"] == order.id
    assert body["status"] == "PENDING"


def test_store_order_grand_total_matches_the_salesman_form_numbers(client, db):
    _, _, _, sku = _store(db)
    user, retailer, address = ready_retailer(db)
    admin = make_user(db, EmployeeRole.ADMIN)

    placed = client.post("/store/orders", json=_order_body(sku, address), headers=auth_headers(user))
    assert placed.status_code == 201, placed.text
    store_order = db.query(Order).one()

    # The same SKU and quantity entered the way the salesman form does: unit_price = MRP and a
    # line discount of (MRP - amount) x quantity.
    warehouse_id = store_order.from_entity_id
    reference = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer.id,
            warehouse_id=warehouse_id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=3, unit_price=22.5, discount_amount=13.5)],
        ),
        admin,
    )
    db.refresh(store_order)
    assert calculate_order_totals(store_order)["grand_total"] == calculate_order_totals(reference)["grand_total"]
    assert calculate_order_totals(store_order)["grand_total"] == Decimal("54.00")  # 3 x 18.00


def test_order_detail_is_an_allowlisted_view_of_the_callers_order(client, db):
    _, _, _, sku = _store(db)
    user, _, address = ready_retailer(db)
    headers = auth_headers(user)

    order_id = client.post("/store/orders", json=_order_body(sku, address), headers=headers).json()["id"]
    detail = client.get(f"/store/orders/{order_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()

    assert set(body) == {
        "id", "status", "created_at", "lines", "subtotal", "total", "ship_to", "invoice_available",
    }
    assert body["status"] == "PENDING"
    assert body["invoice_available"] is False
    assert body["subtotal"] == 54.0 and body["total"] == 54.0
    (line,) = body["lines"]
    assert set(line) == {"sku_id", "name", "image_url", "pack_size", "quantity", "mrp", "line_total"}
    assert line == {
        "sku_id": sku.id,
        "name": "Masala Chips",
        "image_url": "https://jabsons.com/cdn/x.webp",
        "pack_size": "23 g",
        "quantity": 3,
        "mrp": 22.5,
        "line_total": 54.0,
    }
    assert body["ship_to"] == {
        "label": address.label,
        "line1": address.line1,
        "line2": address.line2,
        "landmark": address.landmark,
        "city": address.city,
        "state": address.state,
        "pincode": address.pincode,
    }


def test_order_is_attributed_to_the_retailers_salesman(client, db):
    _, _, _, sku = _store(db)
    salesman = Employee(name="Sam", email="sam@ascend.com", role=EmployeeRole.SALESMAN)
    db.add(salesman)
    db.commit()
    assigned_user, _, assigned_address = ready_retailer(db, assigned_salesman_id=salesman.id)
    self_user, _, self_address = ready_retailer(db)

    assert client.post(
        "/store/orders", json=_order_body(sku, assigned_address, 1), headers=auth_headers(assigned_user)
    ).status_code == 201
    assert client.post(
        "/store/orders", json=_order_body(sku, self_address, 1), headers=auth_headers(self_user)
    ).status_code == 201

    by_retailer = {o.to_entity_id: o for o in db.query(Order).all()}
    assert by_retailer[assigned_user.retailer_id].salesman_id == salesman.id
    assert by_retailer[self_user.retailer_id].salesman_id is None


# --- Task 2: quote, bounds, sanitized stock refusal, own-orders list -------------------------------

from app.models import OrderItemBatch  # noqa: E402
from app.services import store_order as store_order_service  # noqa: E402
from app.services.store_settings import get_store_settings  # noqa: E402


def _counts(db):
    return {
        "orders": db.query(Order).count(),
        "items": db.query(OrderItem).count(),
        "batches": db.query(OrderItemBatch).count(),
        "reserved": sum(b.reserved_quantity for b in db.query(SKUBatch).all()),
    }


def test_quote_prices_lines_like_the_order_and_totals_match_the_placed_order(client, db):
    _, _, _, sku = _store(db)
    user, _, address = ready_retailer(db)
    headers = auth_headers(user)

    quote = client.post(
        "/store/cart/quote",
        json={"items": [{"sku_id": sku.id, "quantity": 2}, {"sku_id": sku.id, "quantity": 1}]},
        headers=headers,
    )
    assert quote.status_code == 200, quote.text
    body = quote.json()
    assert set(body) == {"lines", "subtotal", "total"}
    (line,) = body["lines"]
    assert line == {
        "sku_id": sku.id,
        "name": "Masala Chips",
        "image_url": "https://jabsons.com/cdn/x.webp",
        "pack_size": "23 g",
        "mrp": 22.5,
        "trade_price": 18.0,
        "quantity": 3,
        "line_total": 54.0,
        "max_orderable": 20,
        "within_stock": True,
    }
    assert body["subtotal"] == 54.0 and body["total"] == 54.0

    placed = client.post("/store/orders", json=_order_body(sku, address, 3), headers=headers)
    assert placed.status_code == 201, placed.text
    assert placed.json()["total"] == body["total"]
    assert float(calculate_order_totals(db.query(Order).one())["grand_total"]) == body["total"]


def test_quote_flags_a_line_above_stock_without_refusing(client, db):
    _, _, _, sku = _store(db, qty=5)
    user, _, _ = ready_retailer(db)

    quote = client.post(
        "/store/cart/quote", json={"items": [{"sku_id": sku.id, "quantity": 6}]}, headers=auth_headers(user)
    )
    assert quote.status_code == 200
    (line,) = quote.json()["lines"]
    assert line["max_orderable"] == 5 and line["within_stock"] is False


def test_quantity_bounds_are_whole_packets_1_to_999(client, db):
    _, _, _, sku = _store(db)
    user, _, address = ready_retailer(db)
    headers = auth_headers(user)

    for bad in (0, 1000, 1.5, -1):
        r = client.post("/store/orders", json=_order_body(sku, address, bad), headers=headers)
        assert r.status_code == 422, (bad, r.text)
        q = client.post("/store/cart/quote", json={"items": [{"sku_id": sku.id, "quantity": bad}]}, headers=headers)
        assert q.status_code == 422, (bad, q.text)

    # 1 is within stock and valid; 999 passes validation and is then refused for stock (409, not 422).
    assert client.post("/store/orders", json=_order_body(sku, address, 1), headers=headers).status_code == 201
    assert client.post("/store/orders", json=_order_body(sku, address, 999), headers=headers).status_code == 409
    assert client.post(
        "/store/cart/quote", json={"items": [{"sku_id": sku.id, "quantity": 999}]}, headers=headers
    ).status_code == 200


def test_line_count_bounds_and_duplicate_lines_merge(client, db):
    _, _, _, sku = _store(db, qty=900)
    user, _, address = ready_retailer(db)
    headers = auth_headers(user)

    assert client.post(
        "/store/orders", json={"items": [], "address_id": address.id}, headers=headers
    ).status_code == 422
    too_many = {"items": [{"sku_id": sku.id, "quantity": 1}] * 51, "address_id": address.id}
    assert client.post("/store/orders", json=too_many, headers=headers).status_code == 422

    # Merged before any check: 600 + 600 is over the per-line cap even though each half is fine.
    over = {"items": [{"sku_id": sku.id, "quantity": 600}] * 2, "address_id": address.id}
    assert client.post("/store/orders", json=over, headers=headers).status_code == 422
    assert client.post(
        "/store/cart/quote", json={"items": over["items"]}, headers=headers
    ).status_code == 422

    split = {"items": [{"sku_id": sku.id, "quantity": 2}, {"sku_id": sku.id, "quantity": 3}], "address_id": address.id}
    placed = client.post("/store/orders", json=split, headers=headers)
    assert placed.status_code == 201, placed.text
    assert len(placed.json()["lines"]) == 1 and placed.json()["lines"][0]["quantity"] == 5
    assert db.query(OrderItem).one().quantity == 5


def test_quantity_equal_to_stock_succeeds_and_one_more_is_refused_without_leaking(client, db):
    warehouse, _, _, sku = _store(db, qty=5)
    user, _, address = ready_retailer(db)
    headers = auth_headers(user)

    before = _counts(db)
    refused = client.post("/store/orders", json=_order_body(sku, address, 6), headers=headers)
    assert refused.status_code == 409
    body = refused.json()
    assert body["code"] == "STOCK_CHANGED"
    assert body["items"] == [{"sku_id": sku.id, "max_quantity": 5}]
    assert set(body) == {"code", "message", "items"}
    assert warehouse.name not in refused.text
    assert "available" not in refused.text.lower()
    assert _counts(db) == before

    ok = client.post("/store/orders", json=_order_body(sku, address, 5), headers=headers)
    assert ok.status_code == 201, ok.text
    assert _counts(db)["reserved"] == 5


def test_a_reservation_refused_under_the_lock_is_the_same_sanitized_409(client, db, monkeypatch):
    warehouse, _, _, sku = _store(db, qty=5)
    user, _, address = ready_retailer(db)
    real = store_order_service.storefront_availability
    calls = []

    def over_reporting_first_call(session, sku_ids):
        # The pre-check sees a stale, generous number; the FOR UPDATE reservation then refuses.
        calls.append(1)
        if len(calls) == 1:
            return {sku_id: 999 for sku_id in sku_ids}
        return real(session, sku_ids)

    monkeypatch.setattr(store_order_service, "storefront_availability", over_reporting_first_call)
    before = _counts(db)
    refused = client.post("/store/orders", json=_order_body(sku, address, 6), headers=auth_headers(user))
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "STOCK_CHANGED"
    assert refused.json()["items"] == [{"sku_id": sku.id, "max_quantity": 5}]
    assert warehouse.name not in refused.text and "available" not in refused.text.lower()
    assert _counts(db) == before


def test_unknown_and_unlisted_products_are_named_in_a_422(client, db):
    warehouse, brand, _, sku = _store(db)
    unlisted = stocked_sku(
        db, warehouse=warehouse, brand=brand, category=None, name="Hidden", code="HID-1", mrp="10", amount="8"
    )
    user, _, address = ready_retailer(db)
    headers = auth_headers(user)

    payload = {
        "items": [{"sku_id": sku.id, "quantity": 1}, {"sku_id": unlisted.id, "quantity": 1}, {"sku_id": 987654, "quantity": 1}],
        "address_id": address.id,
    }
    for path, body in (("/store/orders", payload), ("/store/cart/quote", {"items": payload["items"]})):
        r = client.post(path, json=body, headers=headers)
        assert r.status_code == 422, (path, r.text)
        assert r.json()["detail"]["sku_ids"] == sorted([unlisted.id, 987654])
    assert db.query(Order).count() == 0


def test_another_retailers_address_is_a_404(client, db):
    _, _, _, sku = _store(db)
    user, _, _ = ready_retailer(db)
    _, _, foreign_address = ready_retailer(db)

    r = client.post("/store/orders", json=_order_body(sku, foreign_address), headers=auth_headers(user))
    assert r.status_code == 404 and r.json()["detail"] == "Address not found"
    missing = client.post("/store/orders", json={**_order_body(sku, foreign_address), "address_id": 424242}, headers=auth_headers(user))
    assert missing.status_code == 404
    assert db.query(Order).count() == 0


def test_a_closed_store_refuses_quotes_and_orders_with_503(client, db):
    _, _, _, sku = _store(db)
    user, _, address = ready_retailer(db)
    get_store_settings(db).storefront_warehouse_id = None
    db.commit()
    headers = auth_headers(user)

    for path, body in (
        ("/store/orders", _order_body(sku, address)),
        ("/store/cart/quote", {"items": [{"sku_id": sku.id, "quantity": 1}]}),
    ):
        r = client.post(path, json=body, headers=headers)
        assert r.status_code == 503, (path, r.text)
        assert r.json()["detail"] == "Store is not taking orders right now"


def test_orders_list_is_own_orders_newest_first(client, db):
    _, _, _, sku = _store(db)
    user, _, address = ready_retailer(db)
    other, _, other_address = ready_retailer(db)
    headers = auth_headers(user)

    first = client.post("/store/orders", json=_order_body(sku, address, 1), headers=headers).json()["id"]
    second = client.post("/store/orders", json=_order_body(sku, address, 2), headers=headers).json()["id"]
    foreign = client.post("/store/orders", json=_order_body(sku, other_address, 1), headers=auth_headers(other)).json()["id"]

    page = client.get("/store/orders", headers=headers)
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["total"] == 2
    assert [o["id"] for o in body["items"]] == [second, first]
    assert foreign not in [o["id"] for o in body["items"]]
    assert set(body["items"][0]) == {"id", "status", "created_at", "item_count", "total", "invoice_available"}
    assert body["items"][0]["total"] == 36.0 and body["items"][0]["item_count"] == 1
    assert body["items"][0]["status"] == "PENDING" and body["items"][0]["invoice_available"] is False

    assert [o["id"] for o in client.get("/store/orders?limit=1", headers=headers).json()["items"]] == [second]
    assert [o["id"] for o in client.get("/store/orders?limit=1&offset=1", headers=headers).json()["items"]] == [first]
    assert client.get("/store/orders?limit=0", headers=headers).status_code == 422
    assert client.get("/store/orders?limit=101", headers=headers).status_code == 422


def test_another_retailers_order_and_a_missing_id_are_404_never_403(client, db):
    _, _, _, sku = _store(db)
    owner, _, owner_address = ready_retailer(db)
    stranger, _, _ = ready_retailer(db)
    order_id = client.post("/store/orders", json=_order_body(sku, owner_address, 1), headers=auth_headers(owner)).json()["id"]

    foreign = client.get(f"/store/orders/{order_id}", headers=auth_headers(stranger))
    missing = client.get("/store/orders/999999", headers=auth_headers(stranger))
    assert foreign.status_code == 404 and missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": "Order not found"}


# --- Task 3: ship-to stays a snapshot ----------------------------------------------------------------

from app.models import Retailer, RetailerAddress, User  # noqa: E402
from app.models.outlet_delivery import OutletDelivery  # noqa: E402
from app.schemas.order import StatusUpdate  # noqa: E402
from app.services.order import update_order_status  # noqa: E402

SHOP_LAT, SHOP_LNG = 12.971600, 77.594600
NEARBY_LAT, NEARBY_LNG = 12.971870, 77.594600  # ~30 m: inside the 75 m threshold
MOVED_LAT, MOVED_LNG = 12.985000, 77.594600  # ~1.5 km: a real correction for an ordinary order

SHIP_TO_KEYS = (
    "delivery_address_id",
    "ship_to_label",
    "ship_to_line1",
    "ship_to_line2",
    "ship_to_landmark",
    "ship_to_city",
    "ship_to_state",
    "ship_to_pincode",
    "ship_to_latitude",
    "ship_to_longitude",
)


def test_ops_order_detail_shows_where_a_store_order_goes_and_nulls_for_salesman_orders(client, db):
    _, _, _, sku = _store(db)
    user, retailer, address = ready_retailer(db)
    admin = make_user(db, EmployeeRole.ADMIN)
    order_id = client.post("/store/orders", json=_order_body(sku, address), headers=auth_headers(user)).json()["id"]

    detail = client.get(f"/orders/{order_id}", headers=auth_headers(admin))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["delivery_address_id"] == address.id
    assert body["ship_to_label"] == address.label
    assert body["ship_to_line1"] == address.line1
    assert body["ship_to_line2"] == address.line2
    assert body["ship_to_landmark"] == address.landmark
    assert body["ship_to_city"] == address.city
    assert body["ship_to_state"] == address.state
    assert body["ship_to_pincode"] == address.pincode
    assert body["ship_to_latitude"] == address.latitude
    assert body["ship_to_longitude"] == address.longitude

    salesman_order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer.id,
            warehouse_id=db.get(Order, order_id).from_entity_id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=1, unit_price=22.5, discount_amount=4.5)],
        ),
        admin,
    )
    plain = client.get(f"/orders/{salesman_order.id}", headers=auth_headers(admin)).json()
    for key in SHIP_TO_KEYS:
        assert plain[key] is None, key


def test_editing_or_deleting_the_saved_address_never_rewrites_an_existing_order(client, db):
    _, _, _, sku = _store(db)
    user, retailer, address = ready_retailer(db)
    headers = auth_headers(user)
    order_id = client.post("/store/orders", json=_order_body(sku, address), headers=headers).json()["id"]
    original_line1 = address.line1
    original = {key: getattr(db.get(Order, order_id), key) for key in SHIP_TO_KEYS}

    edited = client.patch(
        f"/store/me/addresses/{address.id}",
        json={"line1": "Somewhere else entirely", "latitude": 10.0, "longitude": 20.0},
        headers=headers,
    )
    assert edited.status_code == 200
    db.expire_all()
    order = db.get(Order, order_id)
    assert order.ship_to_line1 == original_line1
    assert {key: getattr(order, key) for key in SHIP_TO_KEYS} == original
    assert client.get(f"/store/orders/{order_id}", headers=headers).json()["ship_to"]["line1"] == original_line1

    # A second address lets the first be deleted (a shop must keep one).
    db.add(RetailerAddress(retailer_id=retailer.id, line1="Second", state="Delhi", latitude=28.5, longitude=77.1))
    db.commit()
    assert client.delete(f"/store/me/addresses/{address.id}", headers=headers).status_code == 204
    db.expire_all()
    order = db.get(Order, order_id)
    assert order.delivery_address_id is None
    expected = {**original, "delivery_address_id": None}
    assert {key: getattr(order, key) for key in SHIP_TO_KEYS} == expected
    assert client.get(f"/store/orders/{order_id}", headers=headers).json()["ship_to"]["line1"] == original_line1


def _drive_to_delivered(db, order, *, lat, lng, accuracy_m=10.0):
    admin = make_user(db, EmployeeRole.ADMIN)
    driver = Employee(name=f"Driver {order.id}", email=f"driver-{order.id}@ascend.com", role=EmployeeRole.DRIVER)
    db.add(driver)
    db.commit()
    driver_user = User(
        email=f"driver-user-{order.id}@ascend.com",
        password_hash="x",
        role=EmployeeRole.DRIVER,
        employee_id=driver.id,
    )
    db.add(driver_user)
    db.commit()
    update_order_status(
        db, order.id, StatusUpdate(status="READY_TO_SHIP", delivery_driver_id=driver.id), current_user=admin
    )
    update_order_status(db, order.id, StatusUpdate(status="OUT_FOR_DELIVERY"), current_user=driver_user)
    update_order_status(
        db,
        order.id,
        StatusUpdate(status="DELIVERED", latitude=lat, longitude=lng, accuracy_m=accuracy_m),
        current_user=driver_user,
    )
    db.refresh(order)


def test_delivering_a_store_order_records_evidence_but_never_moves_the_shop_pin(client, db):
    _, _, _, sku = _store(db)
    user, retailer, address = ready_retailer(db)
    retailer.latitude, retailer.longitude = SHOP_LAT, SHOP_LNG
    db.commit()
    headers = auth_headers(user)

    near_id = client.post("/store/orders", json=_order_body(sku, address, 1), headers=headers).json()["id"]
    far_id = client.post("/store/orders", json=_order_body(sku, address, 1), headers=headers).json()["id"]

    _drive_to_delivered(db, db.get(Order, near_id), lat=NEARBY_LAT, lng=NEARBY_LNG)
    # 1.5 km away is a correction for an ordinary order; for a store order it is only evidence.
    _drive_to_delivered(db, db.get(Order, far_id), lat=MOVED_LAT, lng=MOVED_LNG)

    db.refresh(retailer)
    assert (retailer.latitude, retailer.longitude) == (SHOP_LAT, SHOP_LNG)
    rows = db.query(OutletDelivery).filter(OutletDelivery.retailer_id == retailer.id).order_by(OutletDelivery.id).all()
    assert len(rows) == 2
    assert [r.retailer_updated for r in rows] == [False, False]
    assert (rows[1].driver_latitude, rows[1].driver_longitude) == (MOVED_LAT, MOVED_LNG)


def test_delivering_a_salesman_order_still_moves_the_pin_as_before(client, db):
    _, _, _, sku = _store(db)
    _, retailer, _ = ready_retailer(db)
    retailer.latitude, retailer.longitude = SHOP_LAT, SHOP_LNG
    admin = make_user(db, EmployeeRole.ADMIN)
    db.commit()
    order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer.id,
            warehouse_id=db.query(SKUBatch).first().warehouse_id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=1, unit_price=22.5, discount_amount=4.5)],
        ),
        admin,
    )
    _drive_to_delivered(db, order, lat=MOVED_LAT, lng=MOVED_LNG)

    db.refresh(retailer)
    assert (retailer.latitude, retailer.longitude) == (MOVED_LAT, MOVED_LNG)
    (row,) = db.query(OutletDelivery).filter(OutletDelivery.retailer_id == retailer.id).all()
    assert row.retailer_updated is True


def test_a_brand_new_number_can_order_straight_after_finishing_shop_setup(client, db):
    _, _, _, sku = _store(db)
    user = make_user(db, EmployeeRole.RETAILER, phone_number=9876543211)
    headers = auth_headers(user)

    shop = client.put(
        "/store/me/shop",
        json={
            "shop_name": "Fresh Kirana",
            "address": {"line1": "Shop 1, Lane 2", "state": "Delhi", "latitude": 28.61, "longitude": 77.2},
        },
        headers=headers,
    )
    assert shop.status_code == 200, shop.text
    address_id = shop.json()["addresses"][0]["id"]

    placed = client.post(
        "/store/orders",
        json={"items": [{"sku_id": sku.id, "quantity": 2}], "address_id": address_id},
        headers=headers,
    )
    assert placed.status_code == 201, placed.text
    assert placed.json()["ship_to"]["line1"] == "Shop 1, Lane 2"
    order = db.query(Order).one()
    assert order.salesman_id is None
    assert order.to_entity_id == db.query(Retailer).one().id
