"""Saved delivery addresses (08-08, D-29 / D-30): CRUD scoped to the caller's own shop."""
from app.models import RetailerAddress
from app.models.enums import EmployeeRole
from app.tests.store_helpers import auth_headers, make_retailer, make_user

NEW_ADDRESS = {
    "label": "Godown",
    "line1": "Plot 9, Industrial Area",
    "state": "Haryana",
    "latitude": 28.4595,
    "longitude": 77.0266,
}


def _shop(db, name, phone):
    """A confirmed retailer user with one saved address."""
    retailer = make_retailer(db, name=name, mobile_number=phone)
    user = make_user(db, EmployeeRole.RETAILER, retailer_id=retailer.id, phone_number=phone)
    db.add(
        RetailerAddress(
            retailer_id=retailer.id,
            line1=f"{name} first address",
            state="Delhi",
            latitude=28.6,
            longitude=77.2,
        )
    )
    db.commit()
    return retailer, user


def test_add_list_edit_and_delete_an_address(client, db):
    retailer, user = _shop(db, "Alpha", 9000000001)
    headers = auth_headers(user)

    created = client.post("/store/me/addresses", json=NEW_ADDRESS, headers=headers)
    assert created.status_code == 201, created.text
    new_id = created.json()["id"]
    assert created.json()["label"] == "Godown"

    listed = client.get("/store/me/addresses", headers=headers).json()
    assert len(listed) == 2
    assert listed[0]["id"] == new_id  # newest first
    assert listed[0]["id"] > listed[1]["id"]

    patched = client.patch(
        f"/store/me/addresses/{new_id}", json={"label": "Warehouse", "line2": None}, headers=headers
    )
    assert patched.status_code == 200
    assert patched.json()["label"] == "Warehouse"
    assert patched.json()["line2"] is None
    assert patched.json()["line1"] == "Plot 9, Industrial Area"

    assert client.delete(f"/store/me/addresses/{new_id}", headers=headers).status_code == 204
    assert len(client.get("/store/me/addresses", headers=headers).json()) == 1


def test_deleting_the_only_remaining_address_is_refused(client, db):
    retailer, user = _shop(db, "Alpha", 9000000001)
    only = db.query(RetailerAddress).one()

    response = client.delete(f"/store/me/addresses/{only.id}", headers=auth_headers(user))

    assert response.status_code == 409
    assert response.json()["detail"] == "Keep at least one delivery address"
    assert db.query(RetailerAddress).count() == 1


def test_another_retailers_address_is_a_404_and_never_listed(client, db):
    _, alpha = _shop(db, "Alpha", 9000000001)
    _, bravo = _shop(db, "Bravo", 9000000002)
    alpha_address = (
        db.query(RetailerAddress).filter(RetailerAddress.line1 == "Alpha first address").one()
    )
    headers = auth_headers(bravo)

    patch = client.patch(f"/store/me/addresses/{alpha_address.id}", json={"label": "x"}, headers=headers)
    delete = client.delete(f"/store/me/addresses/{alpha_address.id}", headers=headers)

    assert patch.status_code == 404 and patch.json()["detail"] == "Address not found"
    assert delete.status_code == 404 and delete.json()["detail"] == "Address not found"
    db.expire_all()
    assert db.get(RetailerAddress, alpha_address.id).label is None
    listed = client.get("/store/me/addresses", headers=headers).json()
    assert [row["line1"] for row in listed] == ["Bravo first address"]


def test_a_missing_address_id_is_a_404(client, db):
    _, user = _shop(db, "Alpha", 9000000001)
    response = client.patch("/store/me/addresses/99999", json={"label": "x"}, headers=auth_headers(user))
    assert response.status_code == 404


def test_patch_cannot_clear_required_fields_but_can_change_them(client, db):
    _, user = _shop(db, "Alpha", 9000000001)
    address = db.query(RetailerAddress).one()
    headers = auth_headers(user)
    url = f"/store/me/addresses/{address.id}"

    assert client.patch(url, json={"state": None}, headers=headers).status_code == 422
    assert client.patch(url, json={"latitude": None}, headers=headers).status_code == 422
    assert client.patch(url, json={"line1": None}, headers=headers).status_code == 422
    assert client.patch(url, json={"state": "Nowhere"}, headers=headers).status_code == 422

    ok = client.patch(url, json={"state": "Haryana"}, headers=headers)
    assert ok.status_code == 200
    assert ok.json()["state"] == "Haryana"


def test_post_validates_like_onboarding(client, db):
    _, user = _shop(db, "Alpha", 9000000001)
    headers = auth_headers(user)
    assert client.post("/store/me/addresses", json={**NEW_ADDRESS, "state": "NCT of Delhi"}, headers=headers).status_code == 422
    assert client.post("/store/me/addresses", json={**NEW_ADDRESS, "latitude": 91}, headers=headers).status_code == 422
    assert client.post("/store/me/addresses", json={**NEW_ADDRESS, "pincode": "12345"}, headers=headers).status_code == 422
    ok = client.post("/store/me/addresses", json={**NEW_ADDRESS, "pincode": "122001"}, headers=headers)
    assert ok.status_code == 201
    assert ok.json()["pincode"] == 122001


def test_unfinished_shop_gets_409_and_staff_get_403(client, db):
    new_user = make_user(db, EmployeeRole.RETAILER, phone_number=9000000003)
    admin = make_user(db, EmployeeRole.ADMIN)

    assert client.get("/store/me/addresses", headers=auth_headers(new_user)).status_code == 409
    posted = client.post("/store/me/addresses", json=NEW_ADDRESS, headers=auth_headers(new_user))
    assert posted.status_code == 409
    assert posted.json()["detail"] == "Finish setting up your shop first"

    assert client.get("/store/me/addresses", headers=auth_headers(admin)).status_code == 403
    assert client.get("/store/me", headers=auth_headers(admin)).status_code == 403
    assert client.put(
        "/store/me/shop", json={"shop_name": "x", "address": NEW_ADDRESS}, headers=auth_headers(admin)
    ).status_code == 403
