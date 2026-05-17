from app.core.security import create_access_token, get_password_hash
from app.models import User, Order, Employee, Warehouse
from app.models.enums import EmployeeRole, OrderStatus


def _auth_header_for(db, role: EmployeeRole) -> dict:
    user = User(
        email=f"{role.value.lower()}@example.com",
        password_hash=get_password_hash("password"),
        role=role
    )
    db.add(user)
    db.commit()
    token = create_access_token({"user_id": user.id, "role": role.value})
    return {"Authorization": f"Bearer {token}"}


def test_admin_vs_salesman_access(client, db):
    admin_headers = _auth_header_for(db, EmployeeRole.ADMIN)
    salesman_headers = _auth_header_for(db, EmployeeRole.SALESMAN)

    admin_response = client.post(
        "/admin/brands",
        json={"name": "Brand A", "poc_name": "POC A", "poc_phone_number": 9876543210, "poc_email": "poc-a@example.com"},
        headers=admin_headers
    )
    assert admin_response.status_code == 200

    salesman_response = client.post(
        "/admin/brands",
        json={"name": "Brand B", "poc_name": "POC B", "poc_phone_number": 9876543211, "poc_email": "poc-b@example.com"},
        headers=salesman_headers
    )
    assert salesman_response.status_code == 403


def test_accountant_access(client, db):
    accountant_headers = _auth_header_for(db, EmployeeRole.ACCOUNTANT)
    salesman_headers = _auth_header_for(db, EmployeeRole.SALESMAN)

    order = Order(
        from_entity_type="WAREHOUSE",
        from_entity_id=1,
        to_entity_type="RETAILER",
        to_entity_id=1,
        status=OrderStatus.DELIVERED
    )
    db.add(order)
    db.commit()

    accountant_response = client.post(
        f"/accounting/orders/{order.id}/payments",
        json={"amount": 10, "transaction_reference": "txn-1"},
        headers=accountant_headers
    )
    assert accountant_response.status_code == 200

    salesman_response = client.post(
        f"/accounting/orders/{order.id}/payments",
        json={"amount": 10, "transaction_reference": "txn-2"},
        headers=salesman_headers
    )
    assert salesman_response.status_code == 403


def test_create_warehouse_endpoint_persists_after_auth_dependency_transaction(client, db):
    admin_headers = _auth_header_for(db, EmployeeRole.ADMIN)
    manager = Employee(
        name="Warehouse Manager",
        email="warehouse.manager@example.com",
        role=EmployeeRole.WAREHOUSE_MANAGER,
    )
    db.add(manager)
    db.commit()

    response = client.post(
        "/admin/warehouses",
        json={
            "name": "Central Warehouse",
            "location": "HQ",
            "address_line1": None,
            "address_line2": None,
            "city": None,
            "state": None,
            "pincode": None,
            "manager_id": manager.id,
        },
        headers=admin_headers,
    )

    assert response.status_code == 200
    db.rollback()

    warehouse = db.query(Warehouse).filter(Warehouse.name == "Central Warehouse").first()
    refreshed_manager = db.query(Employee).filter(Employee.id == manager.id).first()
    assert warehouse is not None
    assert refreshed_manager.warehouse_id == warehouse.id


def test_create_warehouse_endpoint_returns_400_for_invalid_manager(client, db):
    admin_headers = _auth_header_for(db, EmployeeRole.ADMIN)
    salesman = Employee(
        name="Salesman",
        email="salesman.manager@example.com",
        role=EmployeeRole.SALESMAN,
    )
    db.add(salesman)
    db.commit()

    response = client.post(
        "/admin/warehouses",
        json={
            "name": "Invalid Warehouse",
            "location": None,
            "address_line1": None,
            "address_line2": None,
            "city": None,
            "state": None,
            "pincode": None,
            "manager_id": salesman.id,
        },
        headers=admin_headers,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Selected user is not a warehouse manager"
