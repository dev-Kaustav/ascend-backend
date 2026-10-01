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
