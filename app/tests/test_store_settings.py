"""08-02: single-row store settings (D-04, D-05, D-32) and their isolation from the company
profile (research Pitfall 4: update_company_profile overwrites every column)."""

import pytest

from app.core.security import create_access_token, get_password_hash
from app.models import StoreSettings, User, Warehouse
from app.models.enums import EmployeeRole
from app.services.store_settings import (
    DEFAULT_DELIVERY_CHARGE_PERCENT,
    DEFAULT_MIN_ORDER_VALUE,
    get_store_settings,
)


def _auth_header_for(db, role: EmployeeRole) -> dict:
    user = User(
        email=f"{role.value.lower()}@settings.test",
        password_hash=get_password_hash("password"),
        role=role,
    )
    db.add(user)
    db.commit()
    token = create_access_token({"user_id": user.id, "role": role.value})
    return {"Authorization": f"Bearer {token}"}


def _admin(db) -> dict:
    return _auth_header_for(db, EmployeeRole.ADMIN)


def _warehouse(db, name="City WH") -> Warehouse:
    warehouse = Warehouse(name=name, location="Delhi", state="Delhi")
    db.add(warehouse)
    db.commit()
    return warehouse


def test_defaults_constants():
    assert str(DEFAULT_MIN_ORDER_VALUE) == "1000.00"
    assert str(DEFAULT_DELIVERY_CHARGE_PERCENT) == "8.00"


def test_get_creates_row_with_defaults(client, db):
    headers = _admin(db)
    assert db.query(StoreSettings).count() == 0
    resp = client.get("/admin/store-settings", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "min_order_value": 1000.0,
        "delivery_charge_percent": 8.0,
        "storefront_warehouse_id": None,
    }
    assert db.query(StoreSettings).count() == 1
    client.get("/admin/store-settings", headers=headers)
    assert db.query(StoreSettings).count() == 1


def test_partial_patch_changes_only_sent_fields(client, db):
    headers = _admin(db)
    warehouse = _warehouse(db)
    client.patch("/admin/store-settings", json={"delivery_charge_percent": 12.5, "storefront_warehouse_id": warehouse.id}, headers=headers)

    resp = client.patch("/admin/store-settings", json={"min_order_value": 1500}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "min_order_value": 1500.0,
        "delivery_charge_percent": 12.5,
        "storefront_warehouse_id": warehouse.id,
    }


def test_patch_sets_warehouse_and_clears_it(client, db):
    headers = _admin(db)
    warehouse = _warehouse(db)
    resp = client.patch("/admin/store-settings", json={"storefront_warehouse_id": warehouse.id}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["storefront_warehouse_id"] == warehouse.id
    resp = client.patch("/admin/store-settings", json={"storefront_warehouse_id": None}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["storefront_warehouse_id"] is None


def test_unknown_warehouse_is_400(client, db):
    resp = client.patch("/admin/store-settings", json={"storefront_warehouse_id": 999999}, headers=_admin(db))
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Warehouse not found"


@pytest.mark.parametrize(
    "field,value,ok",
    [
        ("min_order_value", 0, True),
        ("min_order_value", -0.01, False),
        ("delivery_charge_percent", 0, True),
        ("delivery_charge_percent", 100, True),
        ("delivery_charge_percent", -0.01, False),
        ("delivery_charge_percent", 100.01, False),
        ("min_order_value", None, False),
        ("delivery_charge_percent", None, False),
        ("min_order_value", 10.123, False),
        ("delivery_charge_percent", 8.123, False),
    ],
)
def test_money_boundaries(client, db, field, value, ok):
    headers = _admin(db)
    client.get("/admin/store-settings", headers=headers)
    resp = client.patch("/admin/store-settings", json={field: value}, headers=headers)
    assert resp.status_code == (200 if ok else 422), resp.text
    if ok:
        assert resp.json()[field] == float(value)
    else:
        db.expire_all()
        current = get_store_settings(db)
        assert float(getattr(current, field)) in (1000.0, 8.0)


@pytest.mark.parametrize("role", [EmployeeRole.SALESMAN, EmployeeRole.ACCOUNTANT, EmployeeRole.RETAILER])
def test_store_settings_are_admin_only(client, db, role):
    headers = _auth_header_for(db, role)
    assert client.get("/admin/store-settings", headers=headers).status_code == 403
    assert client.patch("/admin/store-settings", json={"min_order_value": 5}, headers=headers).status_code == 403


def test_company_profile_save_never_changes_store_settings(client, db):
    headers = _admin(db)
    warehouse = _warehouse(db)
    client.patch(
        "/admin/store-settings",
        json={"min_order_value": 2500, "delivery_charge_percent": 11, "storefront_warehouse_id": warehouse.id},
        headers=headers,
    )
    full_body = {
        "legal_name": "Ascend Foods Pvt Ltd",
        "gstin": "07AAAAA0000A1Z5",
        "address_line1": "1 Road",
        "address_line2": "",
        "city": "Delhi",
        "state": "Delhi",
        "pincode": "110001",
        "phone": "9999999999",
        "email": "a@b.in",
        "invoice_prefix": "ASC",
        "invoice_footer": "Thanks",
        "bank_name": "HDFC",
        "bank_account_name": "Ascend",
        "bank_account_number": "123",
        "bank_ifsc": "HDFC0000001",
        "bank_branch": "CP",
    }
    resp = client.patch("/admin/company-profile", json=full_body, headers=headers)
    assert resp.status_code == 200, resp.text
    assert client.get("/admin/store-settings", headers=headers).json() == {
        "min_order_value": 2500.0,
        "delivery_charge_percent": 11.0,
        "storefront_warehouse_id": warehouse.id,
    }


def test_company_profile_script_path_never_touches_store_settings(db):
    """scripts/configure_company_profile.py writes through update_company_profile; pin that
    service call too, since the script has no HTTP layer."""
    from app.schemas.admin import CompanyProfileUpdate
    from app.services.admin import update_company_profile

    settings = get_store_settings(db)
    settings.min_order_value = 3333
    db.commit()
    update_company_profile(db, CompanyProfileUpdate(legal_name="Ascend Foods"))
    db.expire_all()
    assert float(get_store_settings(db).min_order_value) == 3333.0
    assert db.query(StoreSettings).count() == 1
