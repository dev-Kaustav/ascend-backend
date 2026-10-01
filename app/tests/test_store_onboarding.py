"""Storefront onboarding (08-08): a shop name and one delivery location are all a retailer needs."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models import Invoice, Order, Retailer, RetailerAddress
from app.models.enums import EmployeeRole, InvoiceStatus, InvoiceType, OrderStatus, SupplyType
from app.tests.store_helpers import auth_headers, make_retailer, make_user

MOBILE = 9876543210

ADDRESS = {
    "line1": "Shop 4, Main Market",
    "state": "Delhi",
    "latitude": 28.6139,
    "longitude": 77.209,
}


def _new_user(db, phone=MOBILE):
    return make_user(db, EmployeeRole.RETAILER, phone_number=phone)


def test_new_number_reports_new_with_nothing_to_show(client, db):
    user = _new_user(db)
    response = client.get("/store/me", headers=auth_headers(user))
    assert response.status_code == 200
    assert response.json() == {
        "mobile": MOBILE,
        "onboarding": "new",
        "shop": None,
        "prefill": None,
        "addresses": [],
    }


def test_new_number_submits_name_and_one_location_and_becomes_ready(client, db):
    user = _new_user(db)
    headers = auth_headers(user)

    response = client.put(
        "/store/me/shop", json={"shop_name": "Sharma Kirana", "address": ADDRESS}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["onboarding"] == "ready"
    assert body["shop"]["name"] == "Sharma Kirana"

    retailers = db.query(Retailer).all()
    assert len(retailers) == 1
    retailer = retailers[0]
    assert retailer.name == "Sharma Kirana"
    assert retailer.mobile_number == MOBILE
    assert retailer.state == "Delhi"
    assert retailer.address_line1 == "Shop 4, Main Market"
    assert retailer.latitude == 28.6139
    assert retailer.longitude == 77.209
    assert retailer.signup_source == "STORE"
    assert retailer.assigned_salesman_id is None
    assert retailer.beat_id is None

    addresses = db.query(RetailerAddress).all()
    assert len(addresses) == 1
    assert addresses[0].retailer_id == retailer.id
    assert addresses[0].line1 == "Shop 4, Main Market"
    assert addresses[0].state == "Delhi"
    assert addresses[0].latitude == 28.6139
    assert addresses[0].longitude == 77.209

    db.refresh(user)
    assert user.retailer_id == retailer.id
    assert user.shop_confirmed_at is not None

    me = client.get("/store/me", headers=headers).json()
    assert me["onboarding"] == "ready"
    assert me["shop"]["retailer_id"] == retailer.id
    assert len(me["addresses"]) == 1
    assert me["prefill"] is None


def test_submitting_the_same_shop_twice_leaves_one_retailer_and_one_address(client, db):
    user = _new_user(db)
    headers = auth_headers(user)
    payload = {"shop_name": "Sharma Kirana", "address": ADDRESS}

    assert client.put("/store/me/shop", json=payload, headers=headers).status_code == 200
    assert client.put("/store/me/shop", json=payload, headers=headers).status_code == 200

    assert db.query(Retailer).count() == 1
    assert db.query(RetailerAddress).count() == 1


def test_admin_token_is_refused_on_store_me(client, db):
    admin = make_user(db, EmployeeRole.ADMIN)
    response = client.get("/store/me", headers=auth_headers(admin))
    assert response.status_code == 403
    assert response.json()["detail"] == "Retailer account required"


# --- Confirm path and input validation (Task 2) ---------------------------------------------

def _salesman_shop(db, **overrides):
    retailer = make_retailer(db, name="Gupta General Store", state="Haryana", mobile_number=MOBILE)
    retailer.address_line1 = "12 Old Road"
    retailer.city = "Gurugram"
    retailer.pincode = 122001
    retailer.gst_number = "06ABCDE1234F1Z5"
    retailer.latitude = 28.45
    retailer.longitude = 77.02
    for key, value in overrides.items():
        setattr(retailer, key, value)
    db.commit()
    return retailer


def _unconfirmed_user(db, retailer):
    return make_user(
        db, EmployeeRole.RETAILER, retailer_id=retailer.id, phone_number=MOBILE, confirmed=False
    )


def _issue_invoice_for(db, retailer):
    order = Order(
        from_entity_type="BRAND",
        from_entity_id=1,
        to_entity_type="RETAILER",
        to_entity_id=retailer.id,
        status=OrderStatus.OUT_FOR_DELIVERY,
    )
    db.add(order)
    db.flush()
    invoice = Invoice(
        invoice_number="INV-0001",
        invoice_date=datetime.now(timezone.utc),
        status=InvoiceStatus.ISSUED.value,
        order_id=order.id,
        invoice_type=InvoiceType.B2C.value,
        supply_type=SupplyType.REGULAR.value,
        reverse_charge=False,
        place_of_supply="Haryana",
        is_inter_state=False,
        supplier_legal_name="Ascend Foods",
        buyer_name=retailer.name,
        buyer_address=retailer.address_line1,
        taxable_value=Decimal("100.00"),
        discount_amount=Decimal("0.00"),
        cgst_amount=Decimal("9.00"),
        sgst_amount=Decimal("9.00"),
        igst_amount=Decimal("0.00"),
        cess_amount=Decimal("0.00"),
        total_tax_amount=Decimal("18.00"),
        grand_total=Decimal("118.00"),
    )
    db.add(invoice)
    db.commit()
    return invoice


def test_confirm_state_returns_prefill_from_the_existing_retailer(client, db):
    retailer = _salesman_shop(db)
    user = _unconfirmed_user(db, retailer)

    body = client.get("/store/me", headers=auth_headers(user)).json()

    assert body["onboarding"] == "confirm"
    assert body["shop"] is None
    assert body["prefill"] == {
        "shop_name": "Gupta General Store",
        "address_line1": "12 Old Road",
        "address_line2": None,
        "city": "Gurugram",
        "state": "Haryana",
        "pincode": 122001,
        "gst_number": "06ABCDE1234F1Z5",
        "latitude": 28.45,
        "longitude": 77.02,
    }


def test_confirm_edits_the_same_retailer_and_leaves_issued_invoices_untouched(client, db):
    retailer = _salesman_shop(db)
    retailer_id = retailer.id
    user = _unconfirmed_user(db, retailer)
    invoice = _issue_invoice_for(db, retailer)
    before = (invoice.buyer_name, invoice.buyer_address)

    response = client.put(
        "/store/me/shop",
        json={
            "shop_name": "Gupta Mega Mart",
            "gst_number": "06ABCDE1234F1Z5",
            "address": {**ADDRESS, "state": "Haryana", "line1": "99 New Road"},
        },
        headers=auth_headers(user),
    )

    assert response.status_code == 200, response.text
    assert response.json()["onboarding"] == "ready"
    assert db.query(Retailer).count() == 1
    db.expire_all()
    retailer = db.get(Retailer, retailer_id)
    assert retailer.name == "Gupta Mega Mart"
    assert retailer.address_line1 == "99 New Road"
    assert retailer.latitude == 28.6139
    assert db.query(RetailerAddress).count() == 1
    invoice = db.query(Invoice).one()
    assert (invoice.buyer_name, invoice.buyer_address) == before == ("Gupta General Store", "12 Old Road")


def test_confirm_does_not_add_an_address_when_one_is_already_saved(client, db):
    retailer = _salesman_shop(db)
    user = _unconfirmed_user(db, retailer)
    db.add(
        RetailerAddress(
            retailer_id=retailer.id, line1="Godown", state="Haryana", latitude=1.0, longitude=2.0
        )
    )
    db.commit()

    response = client.put(
        "/store/me/shop",
        json={"shop_name": "Gupta General Store", "address": ADDRESS},
        headers=auth_headers(user),
    )

    assert response.status_code == 200
    assert db.query(RetailerAddress).count() == 1


def test_confirm_without_an_address_is_refused(client, db):
    retailer = _salesman_shop(db)
    user = _unconfirmed_user(db, retailer)

    response = client.put(
        "/store/me/shop", json={"shop_name": "Gupta General Store"}, headers=auth_headers(user)
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "A delivery location is required"
    db.refresh(user)
    assert user.shop_confirmed_at is None


def test_new_number_without_an_address_is_refused(client, db):
    user = _new_user(db)
    response = client.put("/store/me/shop", json={"shop_name": "Sharma Kirana"}, headers=auth_headers(user))
    assert response.status_code == 422
    assert response.json()["detail"] == "A delivery location is required"
    assert db.query(Retailer).count() == 0


def test_ready_shop_ignores_an_address_and_only_updates_name_and_gstin(client, db):
    user = _new_user(db)
    headers = auth_headers(user)
    client.put("/store/me/shop", json={"shop_name": "Sharma Kirana", "address": ADDRESS}, headers=headers)

    response = client.put(
        "/store/me/shop",
        json={
            "shop_name": "Sharma Mart",
            "gst_number": "07ABCDE1234F1Z5",
            "address": {**ADDRESS, "line1": "Elsewhere", "state": "Punjab"},
        },
        headers=headers,
    )

    assert response.status_code == 200
    retailer = db.query(Retailer).one()
    db.refresh(retailer)
    assert retailer.name == "Sharma Mart"
    assert retailer.gst_number == "07ABCDE1234F1Z5"
    assert retailer.address_line1 == "Shop 4, Main Market"
    assert retailer.state == "Delhi"
    assert db.query(RetailerAddress).count() == 1


@pytest.mark.parametrize(
    "address_patch",
    [
        {"state": "NCT of Delhi"},
        {"latitude": 90.0001},
        {"latitude": -90.0001},
        {"longitude": 180.5},
        {"pincode": "11001"},
        {"pincode": "11000a"},
        {"line1": "   "},
    ],
)
def test_bad_address_values_are_refused_at_the_boundary(client, db, address_patch):
    user = _new_user(db)
    response = client.put(
        "/store/me/shop",
        json={"shop_name": "Sharma Kirana", "address": {**ADDRESS, **address_patch}},
        headers=auth_headers(user),
    )
    assert response.status_code == 422
    assert db.query(Retailer).count() == 0


@pytest.mark.parametrize("missing", ["line1", "state", "latitude", "longitude"])
def test_each_required_address_field_is_required(client, db, missing):
    user = _new_user(db)
    address = {k: v for k, v in ADDRESS.items() if k != missing}
    response = client.put(
        "/store/me/shop", json={"shop_name": "Sharma Kirana", "address": address}, headers=auth_headers(user)
    )
    assert response.status_code == 422


def test_blank_shop_name_is_refused(client, db):
    user = _new_user(db)
    response = client.put(
        "/store/me/shop", json={"shop_name": "   ", "address": ADDRESS}, headers=auth_headers(user)
    )
    assert response.status_code == 422


def test_boundary_longitude_is_accepted_and_coordinates_are_stored_exactly(client, db):
    user = _new_user(db)
    address = {**ADDRESS, "longitude": -180, "latitude": 28.613912345678}
    response = client.put(
        "/store/me/shop", json={"shop_name": "Sharma Kirana", "address": address}, headers=auth_headers(user)
    )
    assert response.status_code == 200
    assert db.query(Retailer).one().latitude == 28.613912345678
    assert db.query(RetailerAddress).one().longitude == -180


def test_gstin_is_uppercased_and_blank_becomes_null(client, db):
    user = _new_user(db)
    headers = auth_headers(user)
    client.put(
        "/store/me/shop",
        json={"shop_name": "Sharma Kirana", "gst_number": "07abcde1234f1z5", "address": ADDRESS},
        headers=headers,
    )
    assert db.query(Retailer).one().gst_number == "07ABCDE1234F1Z5"

    other = _new_user(db, phone=9123456780)
    client.put(
        "/store/me/shop",
        json={"shop_name": "Other", "gst_number": "", "address": ADDRESS},
        headers=auth_headers(other),
    )
    assert db.query(Retailer).filter(Retailer.name == "Other").one().gst_number is None


@pytest.mark.parametrize("gst", ["07ABCDE1234F1Z", "07ABCDE1234F1Z55", "07ABCDE-234F1Z5"])
def test_malformed_gstin_is_refused(client, db, gst):
    user = _new_user(db)
    response = client.put(
        "/store/me/shop",
        json={"shop_name": "Sharma Kirana", "gst_number": gst, "address": ADDRESS},
        headers=auth_headers(user),
    )
    assert response.status_code == 422


def test_minimal_body_succeeds_on_the_confirm_path_too(client, db):
    retailer = _salesman_shop(db, gst_number=None)
    user = _unconfirmed_user(db, retailer)
    response = client.put(
        "/store/me/shop", json={"shop_name": "Gupta", "address": ADDRESS}, headers=auth_headers(user)
    )
    assert response.status_code == 200
    assert response.json()["onboarding"] == "ready"


@pytest.mark.parametrize("body_extra", [{}, {"gst_number": None}, {"gst_number": ""}])
def test_confirm_with_an_omitted_gstin_keeps_the_existing_one(client, db, body_extra):
    """WR-04: the shared Retailer record keeps its GSTIN when the client does not send one."""
    retailer = _salesman_shop(db)  # carries 06ABCDE1234F1Z5
    retailer_id = retailer.id
    user = _unconfirmed_user(db, retailer)
    response = client.put(
        "/store/me/shop",
        json={"shop_name": "Gupta Mega Mart", "address": ADDRESS, **body_extra},
        headers=auth_headers(user),
    )
    assert response.status_code == 200, response.text
    assert response.json()["onboarding"] == "ready"
    db.expire_all()
    assert db.get(Retailer, retailer_id).gst_number == "06ABCDE1234F1Z5"


def test_confirm_with_an_explicit_gstin_still_updates_it(client, db):
    retailer = _salesman_shop(db)
    retailer_id = retailer.id
    user = _unconfirmed_user(db, retailer)
    response = client.put(
        "/store/me/shop",
        json={"shop_name": "Gupta Mega Mart", "gst_number": "07ABCDE1234F1Z5", "address": ADDRESS},
        headers=auth_headers(user),
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(Retailer, retailer_id).gst_number == "07ABCDE1234F1Z5"
