from uuid import uuid4

import pytest

from app.core.security import create_access_token
from app.models import Account, Brand, Employee, Inventory, OrderTrail, Retailer, SKU, SKUBatch, User, Warehouse
from app.models.enums import EmployeeRole, OrderStatus, PaymentStatus
from app.schemas.order import OrderCreate, OrderItemCreate
from app.services.order import create_outgoing_order, get_order_detail
from app.services.admin import get_orders_page


def headers(user):
    token = create_access_token({"user_id": user.id, "role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def cash_order(db):
    salesman = Employee(name="Cash Salesman", email="cash-salesman@example.com", role=EmployeeRole.SALESMAN)
    other = Employee(name="Other Salesman", email="other-salesman@example.com", role=EmployeeRole.SALESMAN)
    warehouse = Warehouse(name="Cash Warehouse", location="Delhi", state="Delhi")
    brand = Brand(name="Cash Brand")
    db.add_all([salesman, other, warehouse, brand])
    db.flush()
    user = User(email="cash@example.com", password_hash="x", role=EmployeeRole.SALESMAN, employee_id=salesman.id)
    other_user = User(email="other@example.com", password_hash="x", role=EmployeeRole.SALESMAN, employee_id=other.id)
    admin = User(email="admin@example.com", password_hash="x", role=EmployeeRole.ADMIN)
    retailer = Retailer(name="Cash Retailer", state="Delhi", assigned_salesman_id=salesman.id)
    sku = SKU(name="Cash SKU", brand_id=brand.id)
    db.add_all([user, other_user, admin, retailer, sku])
    db.flush()
    db.add_all([
        Inventory(warehouse_id=warehouse.id, sku_id=sku.id, total_quantity=100),
        SKUBatch(warehouse_id=warehouse.id, sku_id=sku.id, quantity_received=100, remaining_quantity=100),
    ])
    db.commit()
    payload = OrderCreate(retailer_id=retailer.id, warehouse_id=warehouse.id, payment_mode="CREDIT", items=[OrderItemCreate(sku_id=sku.id, quantity=1, unit_price=100)])
    order = create_outgoing_order(db, payload, user)
    return dict(order=order, user=user, other=other_user, admin=admin, retailer=retailer, payload=payload)


def collect(client, data, amount="40.00", collection_id=None, user=None):
    return client.post(
        f"/orders/{data['order'].id}/cash-collections",
        json={"amount": amount, "collection_id": str(collection_id or uuid4())},
        headers=headers(user or data["user"]),
    )


def test_partial_and_full_cash_payments_update_balance_and_collector(db, client, cash_order):
    data = cash_order
    first = collect(client, data)
    assert first.status_code == 200, first.text
    assert first.json()["amount"] == 40
    assert first.json()["payment_mode"] == "CASH"
    assert first.json()["collected_by_id"] == data["user"].employee_id
    assert first.json()["collection_date"]
    detail = get_order_detail(db, data["order"].id, data["user"])
    assert detail["pending_amount"] == 60
    assert detail["payment_status"] == "PARTIAL"
    assert detail["can_collect_cash"] is True
    assert detail["status"] == "PENDING"

    assert collect(client, data, "60.00").status_code == 200
    detail = get_order_detail(db, data["order"].id, data["user"])
    assert detail["pending_amount"] == 0
    assert detail["payment_status"] == "PAID"
    assert detail["can_collect_cash"] is False
    assert db.query(Account).count() == 2
    trail = db.query(OrderTrail).filter(OrderTrail.description.like("Cash collected:%")).all()
    assert len(trail) == 2
    assert all(row.changed_by_id == data["user"].id for row in trail)


def test_retry_after_full_payment_is_idempotent(db, client, cash_order):
    key = uuid4()
    first = collect(client, cash_order, "100.00", key)
    second = collect(client, cash_order, "100.00", key)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert db.query(Account).count() == 1
    assert db.query(OrderTrail).filter(OrderTrail.description.like("Cash collected:%")).count() == 1
    assert collect(client, cash_order, "99.00", key).status_code == 400


def test_remaining_balance_is_rechecked_for_each_payment(db, client, cash_order):
    assert collect(client, cash_order, "80.00").status_code == 200
    assert collect(client, cash_order, "30.00").status_code == 400
    assert db.query(Account).count() == 1
    assert get_order_detail(db, cash_order["order"].id, cash_order["user"])["pending_amount"] == 20


@pytest.mark.parametrize("amount", ["0", "-1", "NaN", "Infinity", "1.001", "10000000000.00"])
def test_invalid_amounts_are_rejected(db, client, cash_order, amount):
    assert collect(client, cash_order, amount).status_code == 422
    assert db.query(Account).count() == 0


def test_cancelled_and_paid_orders_cannot_collect_more(db, client, cash_order):
    order = cash_order["order"]
    order.status = OrderStatus.CANCELLED
    db.commit()
    assert collect(client, cash_order).status_code == 400
    assert get_order_detail(db, order.id, cash_order["user"])["can_collect_cash"] is False
    order.status = OrderStatus.DELIVERED
    db.commit()
    assert collect(client, cash_order, "100.00").status_code == 200
    assert collect(client, cash_order, "0.01").status_code == 400
    assert db.query(Account).count() == 1


def test_assigned_salesman_is_not_the_creator_of_admin_orders(db, client, cash_order):
    data = cash_order
    data["payload"].salesman_id = data["user"].employee_id
    admin_order = create_outgoing_order(db, data["payload"], data["admin"])
    assert admin_order.salesman_id == data["user"].employee_id
    detail = get_order_detail(db, admin_order.id, data["user"])
    assert detail["can_collect_cash"] is False
    assert collect(client, {**data, "order": admin_order}).status_code == 403
    assert db.query(Account).count() == 0


def test_creator_keeps_access_after_retailer_reassignment(db, client, cash_order):
    data = cash_order
    data["retailer"].assigned_salesman_id = data["other"].employee_id
    db.commit()
    assert get_order_detail(db, data["order"].id, data["other"])["can_collect_cash"] is False
    assert collect(client, data, user=data["other"]).status_code == 403
    rows, total = get_orders_page(db, data["user"])
    assert total == 1 and rows[0].id == data["order"].id
    assert get_order_detail(db, data["order"].id, data["user"])["can_collect_cash"] is True
    assert collect(client, data).status_code == 200


@pytest.mark.parametrize("role", ["DRIVER", "ACCOUNTANT", "WAREHOUSE_MANAGER", "ADMIN", "BRAND", "RETAILER"])
def test_other_roles_cannot_use_salesman_cash_endpoint(db, client, cash_order, role):
    user = User(email=f"{role}@example.com", password_hash="x", role=role)
    db.add(user)
    db.commit()
    assert collect(client, cash_order, user=user).status_code == 403
    assert db.query(Account).count() == 0


def test_request_cannot_spoof_collector_or_payment_mode(db, client, cash_order):
    response = client.post(
        f"/orders/{cash_order['order'].id}/cash-collections",
        json={"amount": "10.00", "collection_id": str(uuid4()), "collected_by_id": cash_order["other"].employee_id, "payment_mode": "UPI"},
        headers=headers(cash_order["user"]),
    )
    assert response.status_code == 422
    assert db.query(Account).count() == 0
