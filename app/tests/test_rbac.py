from datetime import datetime, timedelta

import pytest
from jose import jwt

from app.core.security import SECRET_KEY
from app.core.security import create_access_token, get_password_hash
from app.models import Group, User, Order, Employee, Warehouse
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


def _expired_auth_header_for(db, role: EmployeeRole) -> dict:
    user = User(
        email=f"expired-{role.value.lower()}@example.com",
        password_hash=get_password_hash("password"),
        role=role,
    )
    db.add(user)
    db.commit()
    token = jwt.encode(
        {
            "user_id": user.id,
            "role": role.value,
            "exp": datetime.utcnow() - timedelta(minutes=1),
        },
        SECRET_KEY,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _header_for_user(user: User, claimed_role: EmployeeRole | None = None) -> dict:
    role = claimed_role or user.role
    token = create_access_token({"user_id": user.id, "role": role.value})
    return {"Authorization": f"Bearer {token}"}


def test_missing_bearer_credentials_returns_401(client):
    response = client.get("/admin/warehouses")

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_wrong_auth_scheme_returns_401(client):
    response = client.get(
        "/admin/warehouses",
        headers={"Authorization": "Basic not-a-bearer-token"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_malformed_bearer_token_returns_401(client):
    response = client.get(
        "/admin/warehouses",
        headers={"Authorization": "Bearer not-a-jwt"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid token"


def test_expired_token_returns_401(client, db):
    response = client.get(
        "/admin/warehouses",
        headers=_expired_auth_header_for(db, EmployeeRole.ADMIN),
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid token"


def test_deleted_user_token_returns_401(client, db):
    user = User(
        email="deleted@example.com",
        password_hash=get_password_hash("password"),
        role=EmployeeRole.ADMIN,
        deleted_at=datetime.utcnow(),
    )
    db.add(user)
    db.commit()

    response = client.get("/admin/warehouses", headers=_header_for_user(user))

    assert response.status_code == 401
    assert response.json()["detail"] == "User deleted"


def test_inactive_user_token_returns_401(client, db):
    user = User(
        email="inactive@example.com",
        password_hash=get_password_hash("password"),
        role=EmployeeRole.ADMIN,
        is_active=False,
    )
    db.add(user)
    db.commit()

    response = client.get("/admin/warehouses", headers=_header_for_user(user))

    assert response.status_code == 401
    assert response.json()["detail"] == "Inactive user"


def test_token_role_mismatch_returns_401(client, db):
    user = User(
        email="role-mismatch@example.com",
        password_hash=get_password_hash("password"),
        role=EmployeeRole.ADMIN,
    )
    db.add(user)
    db.commit()

    response = client.get(
        "/admin/warehouses",
        headers=_header_for_user(user, claimed_role=EmployeeRole.SALESMAN),
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Token role mismatch"


def test_list_groups_counts_only_active_non_deleted_members(client, db):
    group = Group(name="Sales Group", role=EmployeeRole.SALESMAN)
    db.add(group)
    db.flush()
    db.add_all(
        [
            User(
                email="active-group-member@example.com",
                password_hash=get_password_hash("password"),
                role=EmployeeRole.SALESMAN,
                group_id=group.id,
            ),
            User(
                email="inactive-group-member@example.com",
                password_hash=get_password_hash("password"),
                role=EmployeeRole.SALESMAN,
                group_id=group.id,
                is_active=False,
            ),
            User(
                email="deleted-group-member@example.com",
                password_hash=get_password_hash("password"),
                role=EmployeeRole.SALESMAN,
                group_id=group.id,
                deleted_at=datetime.utcnow(),
            ),
        ]
    )
    db.commit()

    response = client.get("/admin/groups", headers=_auth_header_for(db, EmployeeRole.ADMIN))

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": group.id,
            "name": "Sales Group",
            "role": "SALESMAN",
            "user_count": 1,
        }
    ]


@pytest.mark.parametrize(
    ("role", "expected_status"),
    [
        (EmployeeRole.ADMIN, 200),
        (EmployeeRole.SALESMAN, 403),
        (EmployeeRole.ACCOUNTANT, 200),
        (EmployeeRole.WAREHOUSE_MANAGER, 200),
        (EmployeeRole.DRIVER, 403),
        (EmployeeRole.RETAILER, 403),
        (EmployeeRole.BRAND, 403),
    ],
)
def test_warehouse_visibility_role_matrix(client, db, role, expected_status):
    response = client.get("/admin/warehouses", headers=_auth_header_for(db, role))

    assert response.status_code == expected_status


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
