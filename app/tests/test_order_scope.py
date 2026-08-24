import itertools

import pytest

from app.core.security import create_access_token
from app.models import Brand, Employee, Inventory, Order, Retailer, SKU, SKUBatch, User, Warehouse
from app.models.enums import EmployeeRole, OrderStatus
from app.schemas.order import OrderCreate, OrderItemCreate, StatusUpdate
from app.services.admin import get_orders_page
from app.services.order import (
    OrderNotFoundError,
    OrderScopeError,
    create_outgoing_order,
    get_order_detail,
    get_order_for_invoice_pdf,
    get_order_invoice_view,
    update_order_status,
)


_counter = itertools.count()


def _user(db, role, *, employee_id=None, retailer_id=None):
    n = next(_counter)
    user = User(
        email=f"scope-user-{n}@example.com",
        password_hash="x",
        role=role,
        employee_id=employee_id,
        retailer_id=retailer_id,
    )
    db.add(user)
    db.commit()
    return user


def _headers(user):
    token = create_access_token({"user_id": user.id, "role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


def _scope_fixture(db):
    n = next(_counter)
    brand = Brand(name=f"Scope Brand {n}")
    warehouse = Warehouse(name=f"Scope Warehouse {n}", location="Delhi", state="Delhi")
    salesman = Employee(name=f"Scope Salesman {n}", email=f"scope-salesman-{n}@example.com", role=EmployeeRole.SALESMAN)
    driver = Employee(name=f"Scope Driver {n}", email=f"scope-driver-{n}@example.com", role=EmployeeRole.DRIVER)
    other_driver = Employee(name=f"Scope Other Driver {n}", email=f"scope-other-driver-{n}@example.com", role=EmployeeRole.DRIVER)
    db.add_all([brand, warehouse, salesman, driver, other_driver])
    db.commit()

    assigned_retailer = Retailer(
        name=f"Assigned Retailer {n}",
        state="Delhi",
        assigned_salesman_id=salesman.id,
    )
    other_retailer = Retailer(name=f"Other Retailer {n}", state="Delhi")
    db.add_all([assigned_retailer, other_retailer])
    db.commit()

    sku = SKU(name=f"Scope SKU {n}", brand_id=brand.id)
    db.add(sku)
    db.commit()
    db.add_all([
        SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=20, remaining_quantity=20),
        Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=20),
    ])
    db.commit()

    admin = _user(db, EmployeeRole.ADMIN)
    assigned_order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=assigned_retailer.id,
            warehouse_id=warehouse.id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=1, unit_price=10, discount_amount=0)],
        ),
        admin,
    )
    other_order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=other_retailer.id,
            warehouse_id=warehouse.id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=1, unit_price=10, discount_amount=0)],
        ),
        admin,
    )
    assigned_order.delivery_driver_id = driver.id
    other_order.delivery_driver_id = other_driver.id
    db.commit()

    return {
        "admin": admin,
        "assigned_order": assigned_order,
        "other_order": other_order,
        "salesman": _user(db, EmployeeRole.SALESMAN, employee_id=salesman.id),
        "driver": _user(db, EmployeeRole.DRIVER, employee_id=driver.id),
        "other_driver": _user(db, EmployeeRole.DRIVER, employee_id=other_driver.id),
        "retailer": _user(db, EmployeeRole.RETAILER, retailer_id=assigned_retailer.id),
        "other_retailer": _user(db, EmployeeRole.RETAILER, retailer_id=other_retailer.id),
    }


def test_salesman_sees_admin_created_order_for_assigned_retailer(db):
    data = _scope_fixture(db)

    items, total = get_orders_page(db, data["salesman"])

    assert total == 1
    assert [item.id for item in items] == [data["assigned_order"].id]
    assert data["assigned_order"].salesman_id is None
    assert get_order_detail(db, data["assigned_order"].id, data["salesman"])["id"] == data["assigned_order"].id


def test_service_scope_rejects_out_of_scope_and_missing_identity_links(db):
    data = _scope_fixture(db)
    missing_employee = _user(db, EmployeeRole.SALESMAN)
    missing_driver = _user(db, EmployeeRole.DRIVER)
    missing_retailer = _user(db, EmployeeRole.RETAILER)

    with pytest.raises(OrderScopeError):
        get_order_detail(db, data["other_order"].id, data["salesman"])
    with pytest.raises(OrderScopeError):
        get_order_detail(db, data["other_order"].id, data["driver"])
    with pytest.raises(OrderScopeError):
        get_order_detail(db, data["other_order"].id, data["retailer"])
    with pytest.raises(OrderScopeError):
        get_order_invoice_view(db, data["other_order"].id, data["salesman"])
    with pytest.raises(OrderScopeError):
        get_order_for_invoice_pdf(db, data["other_order"].id, data["salesman"])
    with pytest.raises(OrderScopeError):
        get_orders_page(db, missing_employee)
    with pytest.raises(OrderScopeError):
        get_orders_page(db, missing_driver)
    with pytest.raises(OrderScopeError):
        get_orders_page(db, missing_retailer)


def test_public_services_require_actor_and_distinguish_missing_orders(db):
    data = _scope_fixture(db)
    order_id = data["assigned_order"].id
    payload = StatusUpdate(status="CANCELLED")

    for call in (
        lambda: get_orders_page(db),
        lambda: get_order_detail(db, order_id),
        lambda: get_order_invoice_view(db, order_id),
        lambda: get_order_for_invoice_pdf(db, order_id),
        lambda: update_order_status(db, order_id, payload),
    ):
        with pytest.raises(TypeError):
            call()

    for call in (
        lambda: get_order_detail(db, 999999, data["admin"]),
        lambda: get_order_invoice_view(db, 999999, data["admin"]),
        lambda: get_order_for_invoice_pdf(db, 999999, data["admin"]),
        lambda: update_order_status(db, 999999, payload, data["admin"]),
    ):
        with pytest.raises(OrderNotFoundError):
            call()


def test_privileged_roles_can_read_orders(db):
    data = _scope_fixture(db)
    for role in (EmployeeRole.ADMIN, EmployeeRole.ACCOUNTANT, EmployeeRole.WAREHOUSE_MANAGER):
        actor = data["admin"] if role == EmployeeRole.ADMIN else _user(db, role)
        items, total = get_orders_page(db, actor)
        assert total == 2
        assert {item.id for item in items} == {data["assigned_order"].id, data["other_order"].id}


def test_driver_cannot_transition_another_drivers_order_directly(db):
    data = _scope_fixture(db)
    order = data["other_order"]
    order.status = OrderStatus.READY_TO_SHIP
    db.commit()

    with pytest.raises(OrderScopeError):
        update_order_status(db, order.id, StatusUpdate(status="OUT_FOR_DELIVERY"), data["driver"])


def test_update_status_missing_order_returns_404(client, db):
    admin = _user(db, EmployeeRole.ADMIN)

    response = client.patch("/orders/999999/status", json={"status": "CANCELLED"}, headers=_headers(admin))

    assert response.status_code == 404
    assert response.json()["detail"] == "Order not found"


def test_update_status_out_of_scope_returns_403(client, db):
    data = _scope_fixture(db)

    response = client.patch(
        f"/orders/{data['other_order'].id}/status",
        json={"status": "CANCELLED"},
        headers=_headers(data["retailer"]),
    )

    assert response.status_code == 403
    assert "order" in response.json()["detail"].lower()


def test_get_order_routes_map_out_of_scope_access_to_403(client, db):
    data = _scope_fixture(db)
    salesman_without_employee = _user(db, EmployeeRole.SALESMAN)

    list_response = client.get("/orders", headers=_headers(salesman_without_employee))

    assert list_response.status_code == 403
    assert "salesman" in list_response.json()["detail"].lower()

    for route in (
        f"/orders/{data['other_order'].id}",
        f"/orders/{data['other_order'].id}/invoice-view",
        f"/orders/{data['other_order'].id}/invoice.pdf",
    ):
        response = client.get(route, headers=_headers(data["salesman"]))

        assert response.status_code == 403
        assert "order" in response.json()["detail"].lower()


@pytest.mark.parametrize(
    "route",
    (
        "/orders/999999",
        "/orders/999999/invoice-view",
        "/orders/999999/invoice.pdf",
    ),
)
def test_singular_get_order_routes_map_missing_orders_to_404(client, db, route):
    admin = _user(db, EmployeeRole.ADMIN)

    response = client.get(route, headers=_headers(admin))

    assert response.status_code == 404
    assert response.json()["detail"] == "Order not found"


def test_update_status_invalid_transition_returns_400(client, db):
    data = _scope_fixture(db)

    response = client.patch(
        f"/orders/{data['assigned_order'].id}/status",
        json={"status": "DELIVERED"},
        headers=_headers(data["admin"]),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid status transition"


def test_update_status_invalid_driver_returns_400(client, db):
    data = _scope_fixture(db)

    response = client.patch(
        f"/orders/{data['assigned_order'].id}/status",
        json={"status": "READY_TO_SHIP", "delivery_driver_id": 999999},
        headers=_headers(data["admin"]),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid delivery driver"
