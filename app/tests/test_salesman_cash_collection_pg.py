"""Real PostgreSQL proof that simultaneous cash submissions cannot overpay an order.

Uses isolated fixture rows and deletes them by ID in finally, matching the existing PG tests.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import Account, Brand, Employee, Order, OrderItem, OrderTrail, Retailer, SKU, User, Warehouse
from app.models.enums import EmployeeRole, OrderStatus
from app.schemas.order import CashCollectionCreate
from app.services.finance import calculate_order_outstanding
from app.services.order import record_salesman_cash
from app.tests.pg_utils import pg_engine


@pytest.mark.parametrize("same_request", [False, True])
def test_concurrent_cash_submissions_are_serialized(same_request):
    engine = pg_engine()
    try:
        with engine.connect():
            pass
    except Exception:
        if os.getenv("ASCEND_ALLOW_PG_SKIP") == "1":
            pytest.skip("PostgreSQL unreachable and ASCEND_ALLOW_PG_SKIP=1")
        raise
    Session = sessionmaker(bind=engine, autoflush=False)
    suffix = uuid4().hex
    with Session() as db:
        brand = Brand(name=f"Cash race brand {suffix}")
        employee = Employee(name=f"Cash race {suffix}", email=f"cash-race-{suffix}@example.com", role=EmployeeRole.SALESMAN)
        warehouse = Warehouse(name=f"Cash race warehouse {suffix}", state="Delhi")
        retailer = Retailer(name=f"Cash race retailer {suffix}", state="Delhi")
        db.add_all([brand, employee, warehouse, retailer])
        db.flush()
        sku = SKU(name=f"Cash race SKU {suffix}", brand_id=brand.id)
        user = User(email=f"cash-race-{suffix}@example.com", password_hash="x", role=EmployeeRole.SALESMAN, employee_id=employee.id)
        order = Order(from_entity_type="WAREHOUSE", from_entity_id=warehouse.id, to_entity_type="RETAILER", to_entity_id=retailer.id, salesman_id=employee.id, status=OrderStatus.DELIVERED)
        db.add_all([sku, user, order])
        db.flush()
        db.add_all([
            OrderItem(order_id=order.id, sku_id=sku.id, quantity=1, unit_price=100, discount_amount=0),
            OrderTrail(order_id=order.id, order_status=OrderStatus.PENDING, description="Order created", changed_by_id=user.id),
        ])
        ids = {"order": order.id, "user": user.id, "sku": sku.id, "employee": employee.id, "brand": brand.id, "retailer": retailer.id, "warehouse": warehouse.id}
        db.commit()

    barrier = Barrier(2)
    first_key = uuid4()

    def submit(index):
        with Session() as db:
            user = db.get(User, ids["user"])
            request = CashCollectionCreate(amount="100.00" if same_request else "80.00", collection_id=first_key if same_request or index == 0 else uuid4())
            barrier.wait(timeout=10)
            try:
                result = record_salesman_cash(db, ids["order"], request, user)
                return "accepted", result.id
            except ValueError as error:
                return "rejected", str(error)

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(submit, [0, 1]))
        with Session() as db:
            payments = db.query(Account).filter(Account.order_id == ids["order"]).all()
            assert len(payments) == 1
            assert calculate_order_outstanding(db.get(Order, ids["order"])) == (0 if same_request else 20)
            if same_request:
                assert results == [("accepted", payments[0].id)] * 2
            else:
                assert sorted(result[0] for result in results) == ["accepted", "rejected"]
    finally:
        with Session() as db:
            for model, column, key in [
                (Account, Account.order_id, "order"), (OrderTrail, OrderTrail.order_id, "order"),
                (OrderItem, OrderItem.order_id, "order"), (Order, Order.id, "order"),
                (User, User.id, "user"), (Employee, Employee.id, "employee"),
                (SKU, SKU.id, "sku"), (Brand, Brand.id, "brand"),
                (Retailer, Retailer.id, "retailer"), (Warehouse, Warehouse.id, "warehouse"),
            ]:
                db.query(model).filter(column == ids[key]).delete(synchronize_session=False)
            db.commit()
        engine.dispose()
