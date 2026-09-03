import itertools

import pytest

from app.core.security import create_access_token
from app.models import Brand, CompanyProfile, Employee, Retailer, SKU, User, Warehouse
from app.models.enums import EmployeeRole
from app.services.invoice import missing_company_invoice_fields
from app.services.order import OrderScopeError, order_form_lookups

_counter = itertools.count()


def _user(db, role, *, employee_id=None):
    n = next(_counter)
    user = User(
        email=f"lookups-user-{n}@example.com",
        password_hash="x",
        role=role,
        employee_id=employee_id,
    )
    db.add(user)
    db.commit()
    return user


def _headers(user):
    token = create_access_token({"user_id": user.id, "role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def lookups_fixture(db):
    n = next(_counter)
    brand = Brand(name=f"Lookup Brand {n}")
    warehouse = Warehouse(name=f"Lookup Warehouse {n}", location="Delhi", state="Delhi")
    salesman = Employee(name=f"Lookup Salesman {n}", email=f"ls-{n}@example.com", role=EmployeeRole.SALESMAN)
    other_salesman = Employee(name=f"Other Salesman {n}", email=f"os-{n}@example.com", role=EmployeeRole.SALESMAN)
    db.add_all([brand, warehouse, salesman, other_salesman])
    db.commit()

    mine = Retailer(name=f"Mine {n}", state="Delhi", assigned_salesman_id=salesman.id)
    theirs = Retailer(name=f"Theirs {n}", state="Delhi", assigned_salesman_id=other_salesman.id)
    unassigned = Retailer(name=f"Unassigned {n}", state="Delhi")
    db.add_all([mine, theirs, unassigned])
    db.commit()

    sku = SKU(name=f"Lookup SKU {n}", brand_id=brand.id)
    db.add(sku)
    db.commit()

    return {
        "salesman": salesman,
        "mine": mine,
        "theirs": theirs,
        "unassigned": unassigned,
        "warehouse": warehouse,
        "sku": sku,
    }


def test_salesman_lookups_return_only_assigned_retailers(db, lookups_fixture):
    user = _user(db, "SALESMAN", employee_id=lookups_fixture["salesman"].id)

    result = order_form_lookups(db, user)

    names = {r.name for r in result["retailers"]}
    assert names == {lookups_fixture["mine"].name}
    assert lookups_fixture["theirs"].name not in names
    assert lookups_fixture["unassigned"].name not in names
    # A salesman still needs the full catalogue and warehouse list to build a line item.
    assert [s.id for s in result["skus"]] == [lookups_fixture["sku"].id]
    assert [w.id for w in result["warehouses"]] == [lookups_fixture["warehouse"].id]


def test_admin_and_accountant_lookups_return_every_retailer(db, lookups_fixture):
    for role in ("ADMIN", "ACCOUNTANT"):
        result = order_form_lookups(db, _user(db, role))
        names = {r.name for r in result["retailers"]}
        assert lookups_fixture["mine"].name in names
        assert lookups_fixture["theirs"].name in names
        assert lookups_fixture["unassigned"].name in names


def test_salesman_without_employee_record_is_rejected(db, lookups_fixture):
    user = _user(db, "SALESMAN", employee_id=None)
    with pytest.raises(OrderScopeError):
        order_form_lookups(db, user)


def test_lookups_never_expose_the_salesman_roster(db, lookups_fixture):
    """A salesman is forced to themselves by create_outgoing_order, so the form has no
    salesman picker and this endpoint must not hand out the employee list."""
    result = order_form_lookups(db, _user(db, "SALESMAN", employee_id=lookups_fixture["salesman"].id))
    assert "salesmen" not in result
    assert "drivers" not in result
    assert "warehouse_managers" not in result


def test_lookups_endpoint_is_denied_to_roles_that_cannot_create_orders(db, client, lookups_fixture):
    for role in ("WAREHOUSE_MANAGER", "DRIVER"):
        response = client.get("/orders/lookups", headers=_headers(_user(db, role)))
        assert response.status_code == 403, role


def test_lookups_path_is_not_swallowed_by_the_order_id_route(db, client, lookups_fixture):
    """/orders/lookups is declared before /orders/{order_id}; if that ordering regresses
    FastAPI parses "lookups" as an order id and this returns 422, not 200."""
    user = _user(db, "SALESMAN", employee_id=lookups_fixture["salesman"].id)
    response = client.get("/orders/lookups", headers=_headers(user))
    assert response.status_code == 200
    assert [r["name"] for r in response.json()["retailers"]] == [lookups_fixture["mine"].name]


def test_missing_company_invoice_fields_lists_every_blank_supplier_field(db):
    assert missing_company_invoice_fields(None) == [
        "legal_name", "gstin", "state", "address_line1", "pincode",
    ]
    # Whitespace is not a value — a profile saved with " " must still warn.
    blank = CompanyProfile(legal_name="Ascend Foods", gstin="   ", state=None)
    assert "gstin" in missing_company_invoice_fields(blank)
    assert "legal_name" not in missing_company_invoice_fields(blank)

    complete = CompanyProfile(
        legal_name="Ascend Foods",
        gstin="07AAAAA0000A1Z5",
        state="Delhi",
        address_line1="1 Example Road",
        pincode="110001",
    )
    assert missing_company_invoice_fields(complete) == []


def test_company_profile_endpoint_reports_missing_fields(db, client):
    admin = _user(db, "ADMIN")
    response = client.get("/admin/company-profile", headers=_headers(admin))
    assert response.status_code == 200
    # The dev profile is created empty, so the ops UI has something to warn about.
    assert set(response.json()["missing_invoice_fields"]) >= {"gstin", "state"}

    saved = client.patch(
        "/admin/company-profile",
        headers=_headers(admin),
        json={
            "legal_name": "Ascend Foods",
            "gstin": "07AAAAA0000A1Z5",
            "state": "Delhi",
            "address_line1": "1 Example Road",
            "pincode": "110001",
            "invoice_prefix": "ASC",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["missing_invoice_fields"] == []


def test_salesman_can_create_an_order_for_a_retailer_the_lookups_offered(db, client, lookups_fixture):
    """The full journey the salesman order form performs: read /orders/lookups, pick a
    retailer from it, POST /orders. The order must come back stamped with this salesman."""
    from app.models import Inventory, SKUBatch

    warehouse = lookups_fixture["warehouse"]
    sku = lookups_fixture["sku"]
    db.add_all([
        SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=20, remaining_quantity=20),
        Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=20),
    ])
    db.commit()

    salesman = lookups_fixture["salesman"]
    headers = _headers(_user(db, "SALESMAN", employee_id=salesman.id))

    offered = client.get("/orders/lookups", headers=headers).json()
    retailer_id = offered["retailers"][0]["id"]

    response = client.post(
        "/orders",
        headers=headers,
        json={
            "retailer_id": retailer_id,
            "warehouse_id": warehouse.id,
            "payment_mode": "CASH",
            "payment_amount": 100.0,
            # The form sends no salesman_id; even if it did, the service overrides it.
            "items": [{"sku_id": sku.id, "quantity": 2, "unit_price": 50.0, "discount_amount": 0}],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["salesman_id"] == salesman.id


def test_salesman_cannot_create_an_order_for_a_retailer_lookups_withheld(db, client, lookups_fixture):
    """The scoping in order_form_lookups and the scoping in create_outgoing_order must
    agree: a retailer the form never offered is also rejected by the write."""
    headers = _headers(_user(db, "SALESMAN", employee_id=lookups_fixture["salesman"].id))
    response = client.post(
        "/orders",
        headers=headers,
        json={
            "retailer_id": lookups_fixture["theirs"].id,
            "warehouse_id": lookups_fixture["warehouse"].id,
            "payment_mode": "CASH",
            "payment_amount": 100.0,
            "items": [{"sku_id": lookups_fixture["sku"].id, "quantity": 1, "unit_price": 50.0, "discount_amount": 0}],
        },
    )
    assert response.status_code == 403


def test_a_salesman_cannot_attribute_an_order_to_another_salesman(db, client, lookups_fixture):
    from app.models import Inventory, SKUBatch

    warehouse = lookups_fixture["warehouse"]
    sku = lookups_fixture["sku"]
    db.add_all([
        SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=20, remaining_quantity=20),
        Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=20),
    ])
    db.commit()

    salesman = lookups_fixture["salesman"]
    headers = _headers(_user(db, "SALESMAN", employee_id=salesman.id))
    response = client.post(
        "/orders",
        headers=headers,
        json={
            "retailer_id": lookups_fixture["mine"].id,
            "warehouse_id": warehouse.id,
            "payment_mode": "CREDIT",
            "salesman_id": 9999,
            "items": [{"sku_id": sku.id, "quantity": 1, "unit_price": 50.0, "discount_amount": 0}],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["salesman_id"] == salesman.id
