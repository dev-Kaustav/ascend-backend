from app.core.security import create_access_token, get_password_hash
from app.models import User, Order
from app.models.enums import EmployeeRole, OrderType, OrderStatus


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
        json={"name": "Brand A", "contact_info": "test"},
        headers=admin_headers
    )
    assert admin_response.status_code == 200

    salesman_response = client.post(
        "/admin/brands",
        json={"name": "Brand B", "contact_info": "test"},
        headers=salesman_headers
    )
    assert salesman_response.status_code == 403


def test_accountant_access(client, db):
    accountant_headers = _auth_header_for(db, EmployeeRole.ACCOUNTANT)
    salesman_headers = _auth_header_for(db, EmployeeRole.SALESMAN)

    order = Order(
        order_type=OrderType.OUTGOING,
        from_entity_type="WAREHOUSE",
        from_entity_id=1,
        to_entity_type="RETAILER",
        to_entity_id=1,
        status=OrderStatus.CONFIRMED
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
