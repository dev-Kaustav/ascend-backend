"""Storefront onboarding (08-08): a shop name and one delivery location are all a retailer needs."""
from app.models import Retailer, RetailerAddress
from app.models.enums import EmployeeRole
from app.tests.store_helpers import auth_headers, make_user

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
