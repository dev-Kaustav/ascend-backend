"""The whole Phase 8 store journey on the final code (08-13 Task 2).

One scenario over the public HTTP surface only (TestClient). The one test seam is Firebase:
`app.services.firebase_auth.verify_phone_id_token`. Everything else, the catalogue, OTP sign-in,
shop setup, pricing, the delivery charge, dispatch, the invoice, isolation and the login rate
limit, runs the real code. Covers success criteria 2 to 8 of Phase 8.
"""
from decimal import Decimal

from app.core.security import get_password_hash
from app.models import Employee, Invoice, Order, OrderItem, User
from app.models.enums import EmployeeRole
from app.services.store_settings import get_store_settings
from app.tests.store_helpers import (
    auth_headers,
    make_brand,
    make_category,
    make_storefront,
    make_user,
    ready_retailer,
    stocked_sku,
)

PHONE = "+919812345678"
MOBILE = 9812345678
ADDRESS = {
    "line1": "Shop 4, Main Market",
    "city": "Delhi",
    "state": "Delhi",
    "pincode": 110001,
    "latitude": 28.6139,
    "longitude": 77.209,
}


def _all_keys(value):
    """Every dict key anywhere in a decoded JSON document."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from _all_keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _all_keys(inner)


def test_the_whole_store_journey(client, db, monkeypatch):
    # --- the shop's catalogue and settings: one Jabsons SKU, a minimum order, a delivery charge --
    warehouse = make_storefront(db, state="Delhi")
    settings = get_store_settings(db)
    settings.min_order_value = Decimal("1000")
    settings.delivery_charge_percent = Decimal("8")
    db.commit()
    sku = stocked_sku(
        db,
        warehouse=warehouse,
        brand=make_brand(db, "Jabsons"),
        category=make_category(db, "Chips"),
        name="Jabsons Masala Chips",
        code="CHN-MSL-23g",
        mrp="22.5",
        amount="18.0",
        gst_rate=Decimal("12"),
        qty=50,
    )

    # --- criterion 2: anonymous browsing shows the MRP and availability, never trade pricing ---
    anonymous = client.get("/store/products")
    assert anonymous.status_code == 200
    (listed,) = anonymous.json()["items"]
    assert listed["id"] == sku.id and listed["brand"]["name"] == "Jabsons"
    assert listed["mrp"] == 22.5 and listed["in_stock"] is True
    assert "trade_price" not in listed and "max_orderable" not in listed

    # --- criterion 6: OTP sign-in of a brand-new number, then a shop name and one location ------
    monkeypatch.setattr("app.services.firebase_auth.verify_phone_id_token", lambda _token: PHONE)
    signed_in = client.post("/auth/retailer/firebase", json={"id_token": "otp-verified-token"})
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["onboarding"] == "new"
    headers = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}

    ready = client.put(
        "/store/me/shop", json={"shop_name": "Sharma Kirana", "address": ADDRESS}, headers=headers
    )
    assert ready.status_code == 200, ready.text
    assert ready.json()["onboarding"] == "ready"
    address_id = ready.json()["addresses"][0]["id"]

    # --- the signed-in retailer now also sees trade price and the orderable cap -----
    priced = client.get("/store/products", headers=headers).json()["items"][0]
    assert priced["trade_price"] == 18.0
    assert priced["max_orderable"] == 50

    # --- criterion 7: a cart below the minimum carries a rupee delivery charge, no percentage ----
    cart = {"items": [{"sku_id": sku.id, "quantity": 2}]}
    quote = client.post("/store/cart/quote", json=cart, headers=headers)
    assert quote.status_code == 200, quote.text
    quote_body = quote.json()
    assert quote_body["delivery_charge"] > 0
    assert quote_body["free_delivery_above"] == 1000.0
    assert not [key for key in _all_keys(quote_body) if "percent" in key.lower()]

    # --- criterion 3 and 8: priced from the SKU whatever the client claims, to the chosen address -
    forged = {
        "items": [{"sku_id": sku.id, "quantity": 2, "unit_price": 1, "discount_amount": 999}],
        "address_id": address_id,
    }
    placed = client.post("/store/orders", json=forged, headers=headers)
    assert placed.status_code == 201, placed.text
    order_id = placed.json()["id"]
    assert placed.json()["total"] == quote_body["total"]
    assert placed.json()["delivery_charge"] == quote_body["delivery_charge"]
    (item,) = db.query(OrderItem).filter(OrderItem.order_id == order_id).all()
    assert item.unit_price == Decimal("22.50")
    order = db.query(Order).filter(Order.id == order_id).one()
    assert order.to_entity_type == "RETAILER"
    assert order.delivery_charge == Decimal(str(quote_body["delivery_charge"])).quantize(Decimal("0.01"))

    # --- criterion 7: ops dispatch it over the ordinary admin API and the invoice carries the charge
    admin = make_user(db, EmployeeRole.ADMIN)
    driver = Employee(name="Journey Driver", email="journey-driver@ascend.com", role=EmployeeRole.DRIVER)
    db.add(driver)
    db.commit()
    ready_to_ship = client.patch(
        f"/orders/{order_id}/status",
        json={"status": "READY_TO_SHIP", "delivery_driver_id": driver.id},
        headers=auth_headers(admin),
    )
    assert ready_to_ship.status_code == 200, ready_to_ship.text
    dispatched = client.patch(
        f"/orders/{order_id}/status", json={"status": "OUT_FOR_DELIVERY"}, headers=auth_headers(admin)
    )
    assert dispatched.status_code == 200, dispatched.text

    pdf = client.get(f"/store/orders/{order_id}/invoice.pdf", headers=headers)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")

    db.expire_all()
    invoice = db.query(Invoice).filter(Invoice.order_id == order_id).one()
    last_line = sorted(invoice.lines, key=lambda line: line.line_number)[-1]
    assert last_line.description == "Delivery charge"
    assert last_line.sku_id is None
    assert invoice.grand_total == Decimal(str(quote_body["total"])).quantize(Decimal("0.01"))

    # --- criterion 4: a different retailer cannot see this order or its invoice -------
    stranger, _, _ = ready_retailer(db)
    assert client.get(f"/store/orders/{order_id}", headers=auth_headers(stranger)).status_code == 404
    assert (
        client.get(f"/store/orders/{order_id}/invoice.pdf", headers=auth_headers(stranger)).status_code
        == 404
    )

    # --- criterion 5: six bad passwords for one email, the sixth is refused ------------
    db.add(User(email="journey-staff@example.com", password_hash=get_password_hash("right"), role=EmployeeRole.ADMIN))
    db.commit()
    attempts = [
        client.post("/auth/login", json={"email": "journey-staff@example.com", "password": "wrong"}).status_code
        for _ in range(6)
    ]
    assert attempts == [400, 400, 400, 400, 400, 429]
