"""PATCH coverage for the master-data entities the admin console edits in place.

The POSTs for these entities are open to accountants and warehouse managers in places; the
PATCHes deliberately are not, so the role assertions here are the contract, not incidental.
"""

from datetime import timedelta

from app.core.security import create_access_token, get_password_hash
from app.models import Beat, Brand, Employee, Inventory, Retailer, SKU, SKUBatch, User, Warehouse
from app.models.enums import EmployeeRole
from app.schemas.order import OrderCreate, OrderItemCreate
from app.services import inventory as inventory_service
from app.services.order import create_outgoing_order


def _auth_header_for(db, role: EmployeeRole) -> dict:
    user = User(
        email=f"{role.value.lower()}@masterdata.test",
        password_hash=get_password_hash("password"),
        role=role,
    )
    db.add(user)
    db.commit()
    token = create_access_token({"user_id": user.id, "role": role.value})
    return {"Authorization": f"Bearer {token}"}


def _salesman(db, name: str):
    """A SALESMAN user wired to an employee record, which is what the retailer scope keys on."""
    employee = Employee(name=name, email=f"{name.lower().replace(' ', '.')}@masterdata.test", role=EmployeeRole.SALESMAN)
    db.add(employee)
    db.commit()
    user = User(
        email=f"user.{employee.email}",
        password_hash=get_password_hash("password"),
        role=EmployeeRole.SALESMAN,
        employee_id=employee.id,
    )
    db.add(user)
    db.commit()
    token = create_access_token({"user_id": user.id, "role": "SALESMAN"})
    return employee, {"Authorization": f"Bearer {token}"}


def _brand(db, **kwargs) -> Brand:
    brand = Brand(
        name=kwargs.pop("name", "Nimbus"),
        poc_name=kwargs.pop("poc_name", "Asha"),
        poc_phone_number=kwargs.pop("poc_phone_number", 9876543210),
        **kwargs,
    )
    db.add(brand)
    db.commit()
    return brand


def _retailer_payload(name, **overrides):
    body = {
        "name": name,
        "mobile_number": None,
        "address_line1": None,
        "address_line2": None,
        "city": None,
        "state": "Delhi",
        "pincode": None,
        "gst_number": None,
        "assigned_salesman_id": None,
        "beat_id": None,
    }
    body.update(overrides)
    return body


def test_update_brand_changes_only_supplied_fields(client, db):
    brand = _brand(db, poc_email="asha@nimbus.test")
    headers = _auth_header_for(db, EmployeeRole.ADMIN)

    response = client.patch(f"/admin/brands/{brand.id}", json={"name": "Nimbus Foods"}, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Nimbus Foods"
    # Untouched columns survive a partial patch — the point of exclude_unset.
    assert body["poc_name"] == "Asha"
    assert body["poc_phone_number"] == 9876543210
    assert body["poc_email"] == "asha@nimbus.test"


def test_update_retailer_normalises_and_validates(client, db):
    retailer = Retailer(name="Sharma Store", state="Delhi", city="Delhi")
    db.add(retailer)
    db.commit()
    headers = _auth_header_for(db, EmployeeRole.ADMIN)

    ok = client.patch(
        f"/admin/retailers/{retailer.id}",
        json={"city": "Gurugram", "mobile_number": 9812345678, "pincode": 122001},
        headers=headers,
    )
    assert ok.status_code == 200
    assert ok.json()["city"] == "Gurugram"
    assert ok.json()["mobile_number"] == 9812345678
    assert ok.json()["state"] == "Delhi"

    # The validators inherited from RetailerCreate still apply on the update path.
    assert client.patch(
        f"/admin/retailers/{retailer.id}", json={"mobile_number": 123}, headers=headers
    ).status_code == 422
    assert client.patch(
        f"/admin/retailers/{retailer.id}", json={"pincode": 1}, headers=headers
    ).status_code == 422
    assert client.patch(
        f"/admin/retailers/{retailer.id}", json={"state": "Atlantis"}, headers=headers
    ).status_code == 422


def test_update_retailer_beat_is_validated(client, db):
    retailer = Retailer(name="Beat Retailer", state="Delhi")
    beat = Beat(name="North Beat")
    db.add_all([retailer, beat])
    db.commit()
    headers = _auth_header_for(db, EmployeeRole.ADMIN)

    ok = client.patch(f"/admin/retailers/{retailer.id}", json={"beat_id": beat.id}, headers=headers)
    assert ok.status_code == 200
    assert ok.json()["beat_id"] == beat.id

    # Routed through set_retailer_beat, so a dangling beat is rejected rather than written —
    # the SQLite test harness does not enforce the FK, so only that check catches this.
    missing = client.patch(f"/admin/retailers/{retailer.id}", json={"beat_id": 9999}, headers=headers)
    assert missing.status_code == 400


def test_update_sku_rejects_unknown_brand(client, db):
    brand = _brand(db)
    sku = SKU(name="Chips 50g", brand_id=brand.id, mrp=20)
    db.add(sku)
    db.commit()
    headers = _auth_header_for(db, EmployeeRole.ADMIN)

    ok = client.patch(f"/admin/skus/{sku.id}", json={"mrp": 25, "rate": 21}, headers=headers)
    assert ok.status_code == 200
    assert ok.json()["mrp"] == 25
    assert ok.json()["name"] == "Chips 50g"

    bad = client.patch(f"/admin/skus/{sku.id}", json={"brand_id": 9999}, headers=headers)
    assert bad.status_code == 400


def test_update_missing_record_returns_404(client, db):
    headers = _auth_header_for(db, EmployeeRole.ADMIN)

    assert client.patch("/admin/brands/9999", json={"name": "x"}, headers=headers).status_code == 404
    assert client.patch("/admin/retailers/9999", json={"city": "x"}, headers=headers).status_code == 404
    assert client.patch("/admin/skus/9999", json={"mrp": 1}, headers=headers).status_code == 404


def test_master_data_updates_are_admin_only(client, db):
    brand = _brand(db)
    retailer = Retailer(name="RBAC Retailer", state="Delhi")
    db.add(retailer)
    db.commit()
    sku = SKU(name="RBAC SKU", brand_id=brand.id)
    db.add(sku)
    db.commit()

    # One user per role: _auth_header_for derives the email from the role, so reuse the header
    # rather than minting a second user for the same role.
    headers_by_role = {
        role: _auth_header_for(db, role)
        for role in (EmployeeRole.ACCOUNTANT, EmployeeRole.SALESMAN, EmployeeRole.WAREHOUSE_MANAGER)
    }

    for headers in headers_by_role.values():
        assert client.patch(f"/admin/brands/{brand.id}", json={"name": "x"}, headers=headers).status_code == 403
        assert client.patch(f"/admin/skus/{sku.id}", json={"mrp": 1}, headers=headers).status_code == 403

    # Retailers are the exception: a salesman may edit their own, so only the roles with no
    # retailer relationship at all are locked out wholesale.
    for role in (EmployeeRole.ACCOUNTANT, EmployeeRole.WAREHOUSE_MANAGER):
        headers = headers_by_role[role]
        assert client.post("/admin/retailers", json=_retailer_payload("x"), headers=headers).status_code == 403
        assert client.patch(f"/admin/retailers/{retailer.id}", json={"city": "x"}, headers=headers).status_code == 403


def test_editing_sku_price_does_not_rewrite_existing_orders(client, db):
    """Order lines snapshot their own unit_price, so repricing a SKU must not restate history.

    This holds today because order_item carries its own price column. Pinning it here means a
    future change that reads the live SKU price at invoice time fails loudly instead of quietly
    re-pricing every past order.
    """
    brand = _brand(db, name="History Brand")
    warehouse = Warehouse(name="History WH", location="Delhi", state="Delhi")
    retailer = Retailer(name="History Retailer", state="Delhi")
    db.add_all([warehouse, retailer])
    db.commit()
    sku = SKU(name="History SKU", brand_id=brand.id, mrp=100, rate=90)
    db.add(sku)
    db.commit()

    today = inventory_service.current_business_date()
    db.add(
        SKUBatch(
            sku_id=sku.id,
            warehouse_id=warehouse.id,
            expiry_date=today + timedelta(days=60),
            quantity_received=10,
            remaining_quantity=10,
        )
    )
    db.add(Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=10))
    db.commit()

    admin = User(email="history-admin@masterdata.test", password_hash="x", role=EmployeeRole.ADMIN)
    db.add(admin)
    db.commit()
    order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer.id,
            warehouse_id=warehouse.id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=2, unit_price=100, discount_amount=0)],
        ),
        admin,
    )
    original_prices = [item.unit_price for item in order.items]

    headers = {"Authorization": f"Bearer {create_access_token({'user_id': admin.id, 'role': 'ADMIN'})}"}
    repriced = client.patch(f"/admin/skus/{sku.id}", json={"mrp": 250, "rate": 210}, headers=headers)
    assert repriced.status_code == 200
    assert repriced.json()["mrp"] == 250

    db.refresh(order)
    assert [item.unit_price for item in order.items] == original_prices


def test_create_retailer_accepts_and_validates_beat(client, db):
    beat = Beat(name="Create Beat")
    db.add(beat)
    db.commit()
    headers = _auth_header_for(db, EmployeeRole.ADMIN)

    # RetailerCreate declares its optionals without defaults, so every key has to be present
    # even when null — that predates this change and is why the payload is spelled out.
    def payload(name, beat_id):
        return {
            "name": name,
            "mobile_number": None,
            "address_line1": None,
            "address_line2": None,
            "city": None,
            "state": "Delhi",
            "pincode": None,
            "gst_number": None,
            "assigned_salesman_id": None,
            "beat_id": beat_id,
        }

    ok = client.post("/admin/retailers", json=payload("Beat On Create", beat.id), headers=headers)
    assert ok.status_code == 200
    assert ok.json()["beat_id"] == beat.id

    bad = client.post("/admin/retailers", json=payload("Dangling Beat", 9999), headers=headers)
    assert bad.status_code == 400


def test_salesman_create_is_forced_onto_their_own_assignment(client, db):
    mine, headers = _salesman(db, "Ravi")
    other = Employee(name="Other", email="other@masterdata.test", role=EmployeeRole.SALESMAN)
    beat = Beat(name="Someone Else Beat")
    db.add_all([other, beat])
    db.commit()

    # Both assignment fields are supplied and both must be ignored: the retailer lands on the
    # creating salesman with no beat, whatever the client asked for.
    response = client.post(
        "/admin/retailers",
        json=_retailer_payload("Field Shop", assigned_salesman_id=other.id, beat_id=beat.id),
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["assigned_salesman_id"] == mine.id
    assert response.json()["beat_id"] is None


def test_salesman_edits_only_their_own_retailer(client, db):
    mine, headers = _salesman(db, "Ravi")
    other, _ = _salesman(db, "Neha")
    ours = Retailer(name="Ours", state="Delhi", assigned_salesman_id=mine.id)
    theirs = Retailer(name="Theirs", state="Delhi", assigned_salesman_id=other.id)
    db.add_all([ours, theirs])
    db.commit()

    ok = client.patch(f"/admin/retailers/{ours.id}", json={"city": "Noida"}, headers=headers)
    assert ok.status_code == 200
    assert ok.json()["city"] == "Noida"

    denied = client.patch(f"/admin/retailers/{theirs.id}", json={"city": "Noida"}, headers=headers)
    assert denied.status_code == 403


def test_salesman_cannot_reassign_a_retailer_away(client, db):
    mine, headers = _salesman(db, "Ravi")
    other, _ = _salesman(db, "Neha")
    beat = Beat(name="Coverage")
    ours = Retailer(name="Ours", state="Delhi", assigned_salesman_id=mine.id)
    db.add_all([beat, ours])
    db.commit()

    response = client.patch(
        f"/admin/retailers/{ours.id}",
        json={"city": "Noida", "assigned_salesman_id": other.id, "beat_id": beat.id},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["city"] == "Noida"
    # The edit lands, the reassignment does not.
    assert response.json()["assigned_salesman_id"] == mine.id
    assert response.json()["beat_id"] is None


def test_salesman_without_employee_record_is_refused(client, db):
    headers = _auth_header_for(db, EmployeeRole.SALESMAN)

    assert client.post("/admin/retailers", json=_retailer_payload("Orphan"), headers=headers).status_code == 403


def test_admin_can_still_assign_retailers_freely(client, db):
    salesman, _ = _salesman(db, "Ravi")
    beat = Beat(name="North")
    db.add(beat)
    db.commit()
    headers = _auth_header_for(db, EmployeeRole.ADMIN)

    response = client.post(
        "/admin/retailers",
        json=_retailer_payload("Admin Shop", assigned_salesman_id=salesman.id, beat_id=beat.id),
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["assigned_salesman_id"] == salesman.id
    assert response.json()["beat_id"] == beat.id
