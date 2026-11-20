"""The shop screen: GET /retailers/{id} (details and report), GET /retailers/beats, and the orders
list filtered to one shop. Figures come from metrics.py, so a cancelled order counts for nothing."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.models import SKU, Account, Beat, Brand, Employee, Order, OrderItem, Retailer, User
from app.models.enums import EmployeeRole, OrderStatus, PaymentStatus
from app.services import metrics
from app.tests.store_helpers import auth_headers, make_user

TODAY = date(2026, 11, 19)


def _shop(db, name="Gupta Stores", salesman=None, beat=None):
    shop = Retailer(name=name, city="Gurgaon", assigned_salesman_id=salesman.id if salesman else None, beat_id=beat.id if beat else None)
    db.add(shop)
    db.commit()
    return shop


def _order(db, shop, sku, status, quantity, price, when, paid=None):
    order = Order(from_entity_type="WAREHOUSE", from_entity_id=1, to_entity_type="RETAILER", to_entity_id=shop.id,
                  status=status, payment_status=PaymentStatus.CREDIT, created_at=when)
    db.add(order)
    db.flush()
    db.add(OrderItem(order_id=order.id, sku_id=sku.id, quantity=quantity, unit_price=Decimal(price), discount_amount=0))
    if paid:
        db.add(Account(order_id=order.id, amount=Decimal(paid), transaction_reference=f"p{order.id}", collection_date=when))
    db.commit()
    return order


def _sku(db):
    brand = Brand(name="Crax")
    db.add(brand)
    db.flush()
    sku = SKU(name="Peanuts", brand_id=brand.id)
    db.add(sku)
    db.commit()
    return sku


def _salesman_user(db):
    employee = Employee(name="Sumit", email="sumit@ascend.com", role=EmployeeRole.SALESMAN)
    db.add(employee)
    db.flush()
    user = User(email="sumit-login@ascend.com", password_hash="x", role=EmployeeRole.SALESMAN, employee_id=employee.id)
    db.add(user)
    db.commit()
    return employee, user


def _at(days_ago):
    return datetime(2026, 11, 19, 6, 0, tzinfo=timezone.utc) - timedelta(days=days_ago)


def test_report_adds_up_the_shop_and_leaves_cancelled_orders_out(db):
    sku = _sku(db)
    shop = _shop(db)
    _order(db, shop, sku, OrderStatus.DELIVERED, 10, "100", _at(40), paid="1000")   # 10 Oct, paid in full
    _order(db, shop, sku, OrderStatus.DELIVERED, 5, "100", _at(20), paid="200")     # 30 Oct, 300 owed
    _order(db, shop, sku, OrderStatus.PENDING, 2, "100", _at(2))                    # 17 Nov, 200 owed
    _order(db, shop, sku, OrderStatus.CANCELLED, 50, "100", _at(1), paid="500")     # counts for nothing

    report = metrics.retailer_report(db, shop.id, today=TODAY)

    assert report["orders"] == 3
    assert report["ordered_value"] == 1700.0
    assert report["average_order"] == round(1700 / 3, 2)
    assert report["collected"] == 1200.0
    assert report["outstanding"] == 500.0
    assert report["this_month"] == 200.0
    assert report["last_month"] == 1000.0 + 500.0
    assert report["aging"] == {"0_7": 200.0, "7_15": 0.0, "15_30": 300.0, "30_plus": 0.0}
    assert [bill["pending"] for bill in report["unpaid_bills"]] == [300.0, 200.0]  # oldest first


def test_shop_endpoint_is_open_to_the_office_and_to_the_shops_own_salesman_only(client, db):
    employee, salesman = _salesman_user(db)
    beat = Beat(name="DLF Phase 1")
    db.add(beat)
    db.commit()
    own = _shop(db, "Own shop", salesman=employee, beat=beat)
    other = _shop(db, "Someone else's shop")

    admin = make_user(db, EmployeeRole.ADMIN)
    body = client.get(f"/retailers/{own.id}", headers=auth_headers(admin)).json()
    assert (body["name"], body["salesman_name"], body["beat_name"]) == ("Own shop", "Sumit", "DLF Phase 1")
    assert body["report"]["orders"] == 0

    assert client.get(f"/retailers/{own.id}", headers=auth_headers(salesman)).status_code == 200
    assert client.get(f"/retailers/{other.id}", headers=auth_headers(salesman)).status_code == 403
    assert client.get("/retailers/999999", headers=auth_headers(admin)).status_code == 404
    driver = make_user(db, EmployeeRole.DRIVER)
    assert client.get(f"/retailers/{own.id}", headers=auth_headers(driver)).status_code == 403


def test_beats_count_their_shops_and_what_they_owe(client, db):
    sku = _sku(db)
    beat = Beat(name="Sector 14")
    db.add(beat)
    db.commit()
    on_beat = _shop(db, "On the beat", beat=beat)
    _shop(db, "Also on the beat", beat=beat)
    loose = _shop(db, "No beat shop")
    _order(db, on_beat, sku, OrderStatus.DELIVERED, 3, "100", _at(5))
    _order(db, loose, sku, OrderStatus.DELIVERED, 1, "100", _at(5))

    admin = make_user(db, EmployeeRole.ADMIN)
    rows = {row["name"]: row for row in client.get("/retailers/beats", headers=auth_headers(admin)).json()}
    assert (rows["Sector 14"]["shops"], rows["Sector 14"]["outstanding"]) == (2, 300.0)
    assert (rows["No beat"]["id"], rows["No beat"]["shops"], rows["No beat"]["outstanding"]) == (None, 1, 100.0)


def test_shop_list_pages_filters_and_carries_what_each_shop_owes(client, db):
    sku = _sku(db)
    beat = Beat(name="Sector 14")
    db.add(beat)
    db.commit()
    employee, salesman = _salesman_user(db)
    owing = _shop(db, "Bansal Kirana", salesman=employee, beat=beat)
    _order(db, owing, sku, OrderStatus.DELIVERED, 3, "100", _at(5), paid="100")
    _order(db, owing, sku, OrderStatus.CANCELLED, 9, "100", _at(5))
    for name in ("Arora Stores", "Chawla Paan", "Dhingra Mart"):
        _shop(db, name)

    # The latest to order comes first (a cancelled order does not count); never ordered, by name, last.
    dhingra = db.query(Retailer).filter(Retailer.name == "Dhingra Mart").one()
    _order(db, dhingra, sku, OrderStatus.DELIVERED, 1, "100", _at(2), paid="100")
    chawla = db.query(Retailer).filter(Retailer.name == "Chawla Paan").one()
    _order(db, chawla, sku, OrderStatus.CANCELLED, 1, "100", _at(1))

    admin = auth_headers(make_user(db, EmployeeRole.ADMIN))
    page = client.get("/retailers?limit=2&offset=0", headers=admin).json()
    assert page["total"] == 4
    assert [row["name"] for row in page["items"]] == ["Dhingra Mart", "Bansal Kirana"]
    assert (page["items"][1]["outstanding"], page["items"][1]["salesman_name"], page["items"][1]["beat_name"]) == (200.0, "Sumit", "Sector 14")
    assert page["items"][1]["last_order_at"].startswith("2026-11-14")
    rest = client.get("/retailers?limit=2&offset=2", headers=admin).json()["items"]
    assert [(row["name"], row["last_order_at"]) for row in rest] == [("Arora Stores", None), ("Chawla Paan", None)]

    assert client.get("/retailers?search=paan%20chawla", headers=admin).json()["total"] == 1
    assert client.get("/retailers?beat_id=none", headers=admin).json()["total"] == 3
    assert client.get(f"/retailers?beat_id={beat.id}", headers=admin).json()["total"] == 1
    assert client.get(f"/retailers?salesman_id={employee.id}", headers=admin).json()["total"] == 1
    assert client.get("/retailers?beat_id=x", headers=admin).status_code == 422

    mine = client.get("/retailers", headers=auth_headers(salesman)).json()
    assert [row["name"] for row in mine["items"]] == ["Bansal Kirana"]


def test_orders_list_filters_to_one_shop(client, db):
    sku = _sku(db)
    shop = _shop(db, "Filtered shop")
    other = _shop(db, "Other shop")
    mine = _order(db, shop, sku, OrderStatus.PENDING, 1, "100", _at(1))
    _order(db, other, sku, OrderStatus.PENDING, 1, "100", _at(1))

    admin = make_user(db, EmployeeRole.ADMIN)
    body = client.get(f"/orders?retailer_id={shop.id}", headers=auth_headers(admin)).json()
    assert [order["id"] for order in body["items"]] == [mine.id]
