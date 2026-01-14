from datetime import date, datetime, timedelta
from collections import defaultdict
from itertools import count
import re

from app.core.security import get_password_hash
from app.db.session import SessionLocal
from app.models import Brand, Warehouse, Employee, Retailer, SKU, User, Order, SKUBatch, Inventory, OrderItem, OrderItemTax
from app.models.enums import EmployeeRole, OrderType, OrderStatus
from app.schemas.accounting import CreditNoteCreate, CreditNoteItemCreate, PaymentCreate
from app.schemas.admin import IncomingOrderCreate, IncomingOrderItem
from app.schemas.order import OrderCreate, OrderItemCreate, OrderItemTaxCreate, StatusUpdate
from app.services.accounting import create_credit_note, create_payment
from app.services.admin import create_incoming_order
from app.services.order import create_outgoing_order, InsufficientStockError, update_order_status


DEFAULT_PASSWORD = "password"
TOPUP_COUNTER = count(1)

def slugify(value):
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    return normalized or "user"

SKUS = [
    {
        "code": "BlackPepperKaju_18g_MRP30",
        "title": "Jabsons Black Pepper Kaju 18g MRP30",
        "brand": "Jabsons",
        "unit": "g",
        "price": 30.0,
        "cgst": 6.0,
        "sgst": 6.0,
        "igst": 12.0,
    },
    {
        "code": "BlackPepperKaju_35g_MRP60",
        "title": "Jabsons Black Pepper Kaju 35g MRP60",
        "brand": "Jabsons",
        "unit": "g",
        "price": 60.0,
        "cgst": 6.0,
        "sgst": 6.0,
        "igst": 12.0,
    },
    {
        "code": "ChanaJor_30g_MRP10",
        "title": "Jabsons Chana Jor 30g MRP10",
        "brand": "Jabsons",
        "unit": "g",
        "price": 10.0,
        "cgst": 6.0,
        "sgst": 6.0,
        "igst": 12.0,
    },
    {
        "code": "CocktailNuts_15g_MRP10",
        "title": "Jabsons Cocktail Nuts 15g MRP10",
        "brand": "Jabsons",
        "unit": "g",
        "price": 10.0,
        "cgst": 6.0,
        "sgst": 6.0,
        "igst": 12.0,
    },
    {
        "code": "CocktailNuts_30g_MRP20",
        "title": "Jabsons Cocktail Nuts 30g MRP20",
        "brand": "Jabsons",
        "unit": "g",
        "price": 20.0,
        "cgst": 6.0,
        "sgst": 6.0,
        "igst": 12.0,
    },
    {
        "code": "GreenPeaOnion&Garlic_18g_MRP10",
        "title": "Jabsons Green Pea Onion & Garlic 18g MRP10",
        "brand": "Jabsons",
        "unit": "g",
        "price": 10.0,
        "cgst": 6.0,
        "sgst": 6.0,
        "igst": 12.0,
    },
    {
        "code": "RoastedChanaHingJeera_25g_MRP10",
        "title": "Jabsons Roasted Chana Hing Jeera 25g MRP10",
        "brand": "Jabsons",
        "unit": "g",
        "price": 10.0,
        "cgst": 6.0,
        "sgst": 6.0,
        "igst": 12.0,
    },
    {
        "code": "RoastedPeanutsBlackpepper_25g_MRP10",
        "title": "Jabsons Roasted Peanuts Blackpepper 25g MRP10",
        "brand": "Jabsons",
        "unit": "g",
        "price": 10.0,
        "cgst": 6.0,
        "sgst": 6.0,
        "igst": 12.0,
    },
    {
        "code": "RoastedPeanutsHingJeera_25g_MRP10",
        "title": "Jabsons Roasted Peanuts Hing Jeera 25g MRP10",
        "brand": "Jabsons",
        "unit": "g",
        "price": 10.0,
        "cgst": 6.0,
        "sgst": 6.0,
        "igst": 12.0,
    },
    {
        "code": "RoastedPeanutsNimbuPudina_25g_MRP10",
        "title": "Jabsons Roasted Peanuts Nimbu Pudina 25g MRP10",
        "brand": "Jabsons",
        "unit": "g",
        "price": 10.0,
        "cgst": 6.0,
        "sgst": 6.0,
        "igst": 12.0,
    },
    {
        "code": "SaltedKaju_18g_MRP30",
        "title": "Jabsons Salted Kaju 18g MRP30",
        "brand": "Jabsons",
        "unit": "g",
        "price": 30.0,
        "cgst": 6.0,
        "sgst": 6.0,
        "igst": 12.0,
    },
    {
        "code": "SaltedKaju_35g_MRP60",
        "title": "Jabsons Salted Kaju 35g MRP60",
        "brand": "Jabsons",
        "unit": "g",
        "price": 60.0,
        "cgst": 6.0,
        "sgst": 6.0,
        "igst": 12.0,
    },
    {"code": "PeanutClassicSalted_28g_MRP10", "title": "Peanut Classic Salted 28g MRP10", "brand": "Jabsons", "unit": "g", "price": 98.4, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "PeanutTandoori_25g_MRP10", "title": "Peanut Tandoori 25g MRP10", "brand": "Jabsons", "unit": "g", "price": 98.4, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "TandooriRoastedChana_25g_MRP10", "title": "Tandoori Roasted Chana 25g MRP10", "brand": "Jabsons", "unit": "g", "price": 98.4, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "KRChilliAchari_37G_25+12_MRP10", "title": "KR Chilli Achari 37G MRP10", "brand": "Curio", "unit": "g", "price": 96.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "BhootChips_23G_MRP10", "title": "Bhoot Chips 23G MRP10", "brand": "Curio", "unit": "g", "price": 80.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "ChipsAsco_23G_16+7_MRP10", "title": "Chips Asco 23G MRP10", "brand": "Curio", "unit": "g", "price": 80.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "KRNoodelMasala_35G_23+12_MRP10", "title": "KR Noodle Masala 35G MRP10", "brand": "Curio", "unit": "g", "price": 96.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "ChipsPudinaMasla_23G_16+7_MRP10", "title": "Chips Pudina Masala 23G MRP10", "brand": "Curio", "unit": "g", "price": 80.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "TYchipsKasmiriChilliPushpa44gmrp_20", "title": "TY Chips Kashmiri Chilli Pushpa 44g MRP20", "brand": "Curio", "unit": "g", "price": 112.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "TYCHILLI_CHATAKA41GMRP20", "title": "TY Chilli Chataka 41g MRP20", "brand": "Curio", "unit": "g", "price": 112.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "TYChipsPudinaMasla44GMRP20", "title": "TY Chips Pudina Masala 44g MRP20", "brand": "Curio", "unit": "g", "price": 112.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "TYBHOOTKARARE67GMRP20", "title": "TY Bhoot Karare 67g MRP20", "brand": "Curio", "unit": "g", "price": 112.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "TYChipsAsco44GMRP20", "title": "TY Chips Asco 44g MRP20", "brand": "Curio", "unit": "g", "price": 112.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "ORIGINALSTYLE25GMMRP20", "title": "Original Style 25g MRP20", "brand": "Beyond", "unit": "g", "price": 160.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "SALT&PEPPER25GMMPR20", "title": "Salt & Pepper 25g MRP20", "brand": "Beyond", "unit": "g", "price": 160.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "PERIPERI25GMMRP20", "title": "Peri Peri 25g MRP20", "brand": "Beyond", "unit": "g", "price": 160.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "DESIMASALA25GM20", "title": "Desi Masala 25g MRP20", "brand": "Beyond", "unit": "g", "price": 160.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "SnaporaMix_30g_MRP15", "title": "Snapora Metro Mix 30g MRP15", "brand": "Snapora", "unit": "g", "price": 120.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "SnaporaMasala_30g_MRP15", "title": "Snapora Classic Masala 30g MRP15", "brand": "Snapora", "unit": "g", "price": 120.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "SnaporaLime_30g_MRP15", "title": "Snapora Lime Twist 30g MRP15", "brand": "Snapora", "unit": "g", "price": 120.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "GrainyOats_40g_MRP20", "title": "Grainy Oats Crunch 40g MRP20", "brand": "Grainy", "unit": "g", "price": 150.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "GrainyMillet_40g_MRP20", "title": "Grainy Millet Crisp 40g MRP20", "brand": "Grainy", "unit": "g", "price": 150.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "GrainyChilli_40g_MRP20", "title": "Grainy Chilli Burst 40g MRP20", "brand": "Grainy", "unit": "g", "price": 150.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "HarvestPop_35g_MRP12", "title": "Harvest Popcorn 35g MRP12", "brand": "Harvest", "unit": "g", "price": 90.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
    {"code": "HarvestPop_Spicy_35g_MRP12", "title": "Harvest Spicy Popcorn 35g MRP12", "brand": "Harvest", "unit": "g", "price": 90.0, "cgst": 6.0, "sgst": 6.0, "igst": 12.0},
]

EXTRA_SALESMEN = [
    "Aman Verma",
    "Pooja Singh",
    "Rohit Mehta",
    "Nisha Kapoor",
    "Sahil Arora",
    "Meera Nair",
    "Karan Joshi",
    "Divya Rao",
]

RETAILERS = [
    {"name": "DAILY NEEDS DISCOUNT STORE", "phone": 8750867200, "city": "Gurgaon", "state": "Haryana", "pincode": 122001},
    {"name": "FOODSTORIES PRIVATE LIMITED", "phone": 9718003947, "city": "South West Delhi", "state": "Delhi", "pincode": 110070},
    {"name": "LAXMI BHANDAR", "phone": 8470965538, "city": "Gurgaon", "state": "Haryana", "pincode": 122001},
    {"name": "BALA JI DEPARTMENT STORE", "phone": 7532033572, "city": "Gurgaon", "state": "Haryana", "pincode": 122022},
    {"name": "Paan Junction", "phone": 7982541993, "city": "Gurugram", "state": "Haryana", "pincode": 122002},
    {"name": "Nimi Store", "phone": 9991113559, "city": "Gurgaon", "state": "Haryana", "pincode": 122001},
    {"name": "Real Fresh", "phone": 9650696053, "city": "Gurugram", "state": "Haryana", "pincode": 122018},
    {"name": "shri ram g store", "phone": 8802853498, "city": "Gurgaon", "state": "Haryana", "pincode": 122001},
    {"name": "Fresh Basket", "phone": 9876543210, "city": "Noida", "state": "Uttar Pradesh", "pincode": 201301},
    {"name": "Urban Mart", "phone": 9811122233, "city": "Noida", "state": "Uttar Pradesh", "pincode": 201301},
    {"name": "City Superstore", "phone": 9822233344, "city": "Faridabad", "state": "Haryana", "pincode": 121001},
    {"name": "Corner Bazaar", "phone": 9833344455, "city": "Ghaziabad", "state": "Uttar Pradesh", "pincode": 201001},
    {"name": "Green Valley Stores", "phone": 9844455566, "city": "Jaipur", "state": "Rajasthan", "pincode": 302001},
    {"name": "Metro Daily", "phone": 9855566677, "city": "Delhi", "state": "Delhi", "pincode": 110001},
    {"name": "Raja Provisions", "phone": 9866677788, "city": "Mumbai", "state": "Maharashtra", "pincode": 400001},
    {"name": "Shakti Traders", "phone": 9877788899, "city": "Pune", "state": "Maharashtra", "pincode": 411001},
    {"name": "Lotus Kirana", "phone": 9888899900, "city": "Bengaluru", "state": "Karnataka", "pincode": 560001},
    {"name": "Prime Grocers", "phone": 9899900011, "city": "Hyderabad", "state": "Telangana", "pincode": 500001},
    {"name": "Hilltop Stores", "phone": 9900011122, "city": "Chandigarh", "state": "Chandigarh", "pincode": 160001},
    {"name": "Sunrise Retail", "phone": 9911122233, "city": "Lucknow", "state": "Uttar Pradesh", "pincode": 226001},
    {"name": "Lakeview Mart", "phone": 9922233344, "city": "Bhopal", "state": "Madhya Pradesh", "pincode": 462001},
    {"name": "Golden Harvest", "phone": 9933344455, "city": "Indore", "state": "Madhya Pradesh", "pincode": 452001},
    {"name": "Coastal Convenience", "phone": 9944455566, "city": "Chennai", "state": "Tamil Nadu", "pincode": 600001},
]

ORDERS = [
    {
        "invoice": "JABGUR0125116",
        "customer": "RITIKA GENERAL STORE",
        "salesman": "kunal",
        "delivery_status": "DELIVERED",
        "items": [
            {"sku_code": "RoastedPeanutsBlackpepper_25g_MRP10", "quantity": 12.0, "unit_price": 98.4, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "CocktailNuts_15g_MRP10", "quantity": 12.0, "unit_price": 98.4, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "PeanutClassicSalted_28g_MRP10", "quantity": 12.0, "unit_price": 98.4, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "PeanutTandoori_25g_MRP10", "quantity": 12.0, "unit_price": 98.4, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "RoastedPeanutsNimbuPudina_25g_MRP10", "quantity": 12.0, "unit_price": 98.4, "cgst": 6.0, "sgst": 6.0, "igst": None},
        ],
    },
    {
        "invoice": "GUIGUR0125866",
        "customer": "PARMOD PAAN SHOP",
        "salesman": "kunal",
        "delivery_status": "DELIVERED",
        "items": [
            {"sku_code": "KRChilliAchari_37G_25+12_MRP10", "quantity": 12.0, "unit_price": 96.0, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "BhootChips_23G_MRP10", "quantity": 10.0, "unit_price": 80.0, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "ChipsAsco_23G_16+7_MRP10", "quantity": 10.0, "unit_price": 80.0, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "KRNoodelMasala_35G_23+12_MRP10", "quantity": 12.0, "unit_price": 96.0, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "ChipsPudinaMasla_23G_16+7_MRP10", "quantity": 10.0, "unit_price": 80.0, "cgst": 6.0, "sgst": 6.0, "igst": None},
        ],
    },
    {
        "invoice": "GUIGUR0125865",
        "customer": "jalsha pan",
        "salesman": "kunal",
        "delivery_status": "DELIVERED",
        "items": [
            {"sku_code": "TYchipsKasmiriChilliPushpa44gmrp_20", "quantity": 7.0, "unit_price": 112.0, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "TYCHILLI_CHATAKA41GMRP20", "quantity": 7.0, "unit_price": 112.0, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "TYChipsPudinaMasla44GMRP20", "quantity": 7.0, "unit_price": 112.0, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "TYBHOOTKARARE67GMRP20", "quantity": 7.0, "unit_price": 112.0, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "TYChipsAsco44GMRP20", "quantity": 7.0, "unit_price": 112.0, "cgst": 6.0, "sgst": 6.0, "igst": None},
        ],
    },
    {
        "invoice": "JABGUR0125115",
        "customer": "jalsha pan",
        "salesman": "kunal",
        "delivery_status": "DELIVERED",
        "items": [
            {"sku_code": "PeanutClassicSalted_28g_MRP10", "quantity": 24.0, "unit_price": 196.8, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "RoastedChanaHingJeera_25g_MRP10", "quantity": 12.0, "unit_price": 98.4, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "RoastedPeanutsBlackpepper_25g_MRP10", "quantity": 24.0, "unit_price": 196.8, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "TandooriRoastedChana_25g_MRP10", "quantity": 12.0, "unit_price": 98.4, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "PeanutTandoori_25g_MRP10", "quantity": 12.0, "unit_price": 98.4, "cgst": 6.0, "sgst": 6.0, "igst": None},
        ],
    },
    {
        "invoice": "BEYGUR0125547",
        "customer": "Shree Shyam General Store",
        "salesman": "RANISH KUMAR",
        "delivery_status": "DELIVERED",
        "items": [
            {"sku_code": "ORIGINALSTYLE25GMMRP20", "quantity": 10.0, "unit_price": 160.0, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "SALT&PEPPER25GMMPR20", "quantity": 10.0, "unit_price": 160.0, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "PERIPERI25GMMRP20", "quantity": 10.0, "unit_price": 160.0, "cgst": 6.0, "sgst": 6.0, "igst": None},
            {"sku_code": "DESIMASALA25GM20", "quantity": 10.0, "unit_price": 160.0, "cgst": 6.0, "sgst": 6.0, "igst": None},
        ],
    },
]


def get_or_create(db, model, defaults=None, **kwargs):
    instance = db.query(model).filter_by(**kwargs).first()
    if instance:
        return instance, False
    params = dict(kwargs)
    if defaults:
        params.update(defaults)
    instance = model(**params)
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return instance, True


def ensure_employee(db, email, name, role, warehouse_id=None):
    employee = db.query(Employee).filter(Employee.email == email).first()
    if employee:
        employee.name = name
        employee.role = role
        employee.warehouse_id = warehouse_id
        db.commit()
        return employee
    employee = Employee(
        name=name,
        email=email,
        role=role,
        warehouse_id=warehouse_id,
    )
    db.add(employee)
    db.commit()
    db.refresh(employee)
    return employee


def ensure_user(db, email, role, employee_id=None, password=DEFAULT_PASSWORD):
    user = db.query(User).filter(User.email == email).first()
    password_hash = get_password_hash(password)
    if user:
        user.role = role
        user.employee_id = employee_id
        user.password_hash = password_hash
        db.commit()
        return user
    user = User(
        email=email,
        password_hash=password_hash,
        employee_id=employee_id,
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def calculate_order_total(order):
    total = 0
    for item in order.items:
        base = (item.quantity or 0) * (item.unit_price or 0) - (item.discount_amount or 0)
        taxes = 0
        for tax in item.taxes:
            taxes += base * ((tax.rate or 0) / 100)
        total += base + taxes
    return total


def backfill_order_items(db, sku_meta_by_id):
    updated_prices = 0
    added_taxes = 0
    items = db.query(OrderItem).all()
    for item in items:
        meta = sku_meta_by_id.get(item.sku_id)
        if not meta:
            continue
        if not item.unit_price or item.unit_price == 0:
            item.unit_price = meta.get("price", item.unit_price or 0)
            updated_prices += 1
        if not item.taxes:
            if meta.get("cgst"):
                db.add(OrderItemTax(order_item_id=item.id, tax_type="CGST", rate=meta["cgst"]))
            if meta.get("sgst"):
                db.add(OrderItemTax(order_item_id=item.id, tax_type="SGST", rate=meta["sgst"]))
            if meta.get("igst"):
                db.add(OrderItemTax(order_item_id=item.id, tax_type="IGST", rate=meta["igst"]))
            if meta.get("cgst") or meta.get("sgst") or meta.get("igst"):
                added_taxes += 1
    db.commit()
    return updated_prices, added_taxes


def ensure_stock_for_items(db, warehouse_id, items, sku_brand_map, today):
    incoming_by_brand = defaultdict(list)
    for item in items:
        required = item.quantity or 0
        if required <= 0:
            continue
        inventory = db.query(Inventory).filter(
            Inventory.sku_id == item.sku_id,
            Inventory.warehouse_id == warehouse_id
        ).first()
        current_qty = inventory.total_quantity if inventory else 0
        if current_qty >= required:
            continue
        brand_id = sku_brand_map.get(item.sku_id)
        if not brand_id:
            continue
        top_up_qty = max(required * 8, 200)
        incoming_by_brand[brand_id].append(
            IncomingOrderItem(
                sku_id=item.sku_id,
                quantity=top_up_qty,
                batch_number=f"TOPUP-{warehouse_id}-{item.sku_id}-{next(TOPUP_COUNTER)}",
                mfg_date=(today - timedelta(days=30)).isoformat(),
                expiry_date=(today + timedelta(days=300)).isoformat(),
            )
        )

    added_batches = 0
    for brand_id, batch_items in incoming_by_brand.items():
        create_incoming_order(
            db,
            IncomingOrderCreate(brand_id=brand_id, warehouse_id=warehouse_id, items=batch_items)
        )
        added_batches += len(batch_items)
    return added_batches


def main():
    db = SessionLocal()

    warehouse_specs = [
        {"name": "Main Warehouse", "location": "HQ"},
        {"name": "North Hub", "location": "Delhi NCR"},
        {"name": "West Hub", "location": "Mumbai"},
        {"name": "South Hub", "location": "Bengaluru"},
    ]
    warehouses = []
    for spec in warehouse_specs:
        warehouse, _ = get_or_create(db, Warehouse, name=spec["name"], location=spec["location"])
        warehouses.append(warehouse)
    wh_main = warehouses[0]

    admin_emp = ensure_employee(db, "admin@ascend.com", "Admin", EmployeeRole.ADMIN, wh_main.id)
    accountant_emp = ensure_employee(db, "accounts@ascend.com", "Accounts", EmployeeRole.ACCOUNTANT, wh_main.id)
    warehouse_emp = ensure_employee(db, "warehouse@ascend.com", "Warehouse", EmployeeRole.WAREHOUSE_MANAGER, wh_main.id)

    admin_user = ensure_user(db, "admin@ascend.com", EmployeeRole.ADMIN, admin_emp.id)
    ensure_user(db, "accounts@ascend.com", EmployeeRole.ACCOUNTANT, accountant_emp.id)
    ensure_user(db, "warehouse@ascend.com", EmployeeRole.WAREHOUSE_MANAGER, warehouse_emp.id)

    salesman_names = {order["salesman"] for order in ORDERS if order.get("salesman")}
    salesman_names.update(EXTRA_SALESMEN)
    if not salesman_names:
        salesman_names = {"Default Sales"}
    salesmen = {}
    salesman_users = {}
    salesman_list = []
    salesman_user_list = []
    for idx, name in enumerate(sorted(salesman_names)):
        email = f"{slugify(name)}@ascend.local"
        warehouse_id = warehouses[idx % len(warehouses)].id
        employee = ensure_employee(db, email, name, EmployeeRole.SALESMAN, warehouse_id)
        user = ensure_user(db, email, EmployeeRole.SALESMAN, employee.id)
        salesmen[name] = employee
        salesman_users[name] = user
        salesman_list.append(employee)
        salesman_user_list.append(user)

    default_salesman = salesman_list[0]

    brands = {}
    sku_by_code = {}
    for item in SKUS:
        brand = brands.get(item["brand"])
        if not brand:
            brand, _ = get_or_create(db, Brand, name=item["brand"])
            brands[brand.name] = brand
        sku, _ = get_or_create(
            db,
            SKU,
            name=item["title"],
            brand_id=brand.id,
            description=f"SKU: {item['code']}",
            unit=item["unit"],
        )
        sku_by_code[item["code"]] = sku

    for idx, retailer in enumerate(RETAILERS):
        assigned_salesman = salesman_list[idx % len(salesman_list)]
        contact_info = f"Phone: {retailer['phone']} | City: {retailer['city']} | State: {retailer['state']} | Pincode: {retailer['pincode']}"
        retailer_obj, _ = get_or_create(
            db,
            Retailer,
            name=retailer["name"],
            defaults={
                "contact_info": contact_info,
                "assigned_salesman_id": assigned_salesman.id,
            },
        )
        if retailer_obj.assigned_salesman_id is None:
            retailer_obj.assigned_salesman_id = assigned_salesman.id
            db.commit()

    today = date.today()
    existing_inventory_keys = {(inv.sku_id, inv.warehouse_id) for inv in db.query(Inventory).all()}
    for idx, warehouse in enumerate(warehouses):
        incoming_items_by_brand = defaultdict(list)
        qty_primary = 600 if idx == 0 else 350
        qty_secondary = 360 if idx == 0 else 210
        batch_suffix = slugify(warehouse.name)
        for code, sku in sku_by_code.items():
            if (sku.id, warehouse.id) in existing_inventory_keys:
                continue
            incoming_items_by_brand[sku.brand_id].extend(
                [
                    IncomingOrderItem(
                        sku_id=sku.id,
                        quantity=qty_primary,
                        batch_number=f"{code}-{batch_suffix}-B1",
                        mfg_date=(today - timedelta(days=45)).isoformat(),
                        expiry_date=(today + timedelta(days=240)).isoformat(),
                    ),
                    IncomingOrderItem(
                        sku_id=sku.id,
                        quantity=qty_secondary,
                        batch_number=f"{code}-{batch_suffix}-B2",
                        mfg_date=(today - timedelta(days=20)).isoformat(),
                        expiry_date=(today + timedelta(days=360)).isoformat(),
                    ),
                ]
            )

    for brand_id, items in incoming_items_by_brand.items():
        create_incoming_order(
            db,
            IncomingOrderCreate(brand_id=brand_id, warehouse_id=warehouse.id, items=items)
        )

    sku_meta_by_id = {}
    for meta in SKUS:
        sku = sku_by_code.get(meta["code"])
        if sku:
            sku_meta_by_id[sku.id] = meta
    sku_brand_map = {sku.id: sku.brand_id for sku in sku_by_code.values()}

    created_orders = 0
    topup_batches = 0
    for idx, order_data in enumerate(ORDERS):
        if db.query(Order).filter(Order.invoice_number == order_data["invoice"]).first():
            continue
        salesman = salesmen.get(order_data["salesman"], default_salesman)
        salesman_user = salesman_users.get(order_data["salesman"], admin_user)
        retailer, _ = get_or_create(
            db,
            Retailer,
            name=order_data["customer"],
            defaults={"contact_info": f"Customer from {order_data['invoice']}", "assigned_salesman_id": salesman.id},
        )
        if retailer.assigned_salesman_id is None:
            retailer.assigned_salesman_id = salesman.id
            db.commit()
        elif retailer.assigned_salesman_id != salesman.id:
            retailer.assigned_salesman_id = salesman.id
            db.commit()

        items = []
        for item in order_data["items"]:
            sku = sku_by_code.get(item["sku_code"])
            if not sku:
                brand, _ = get_or_create(db, Brand, name="Generic")
                sku, _ = get_or_create(
                    db,
                    SKU,
                    name=item["sku_code"],
                    brand_id=brand.id,
                    description=f"SKU: {item['sku_code']}",
                    unit="unit",
                )
                sku_by_code[item["sku_code"]] = sku
            taxes = []
            if item.get("cgst"):
                taxes.append(OrderItemTaxCreate(tax_type="CGST", rate=item["cgst"]))
            if item.get("sgst"):
                taxes.append(OrderItemTaxCreate(tax_type="SGST", rate=item["sgst"]))
            if item.get("igst"):
                taxes.append(OrderItemTaxCreate(tax_type="IGST", rate=item["igst"]))
            items.append(
                OrderItemCreate(
                    sku_id=sku.id,
                    quantity=item["quantity"],
                    unit_price=item["unit_price"],
                    discount_amount=0,
                    taxes=taxes,
                )
            )
        if not items:
            continue

        warehouse_id = order_data.get("warehouse_id") or salesman.warehouse_id or wh_main.id
        topup_batches += ensure_stock_for_items(db, warehouse_id, items, sku_brand_map, today)
        order_payload = OrderCreate(retailer_id=retailer.id, warehouse_id=warehouse_id, items=items)
        try:
            order = create_outgoing_order(db, order_payload, salesman_user)
        except InsufficientStockError:
            continue

        if not order.invoice_number:
            order.invoice_number = order_data["invoice"]
            db.commit()

        update_order_status(db, order.id, StatusUpdate(status="CONFIRMED"))
        if "DELIVERED" in order_data["delivery_status"].upper():
            update_order_status(db, order.id, StatusUpdate(status="DELIVERED"))

        order_date = today - timedelta(days=(idx * 12 + 5))
        order.created_at = datetime.combine(order_date, datetime.min.time())
        db.commit()

        created_orders += 1

    retailer_list = db.query(Retailer).order_by(Retailer.id).all()
    retailers_by_salesman = defaultdict(list)
    for retailer in retailer_list:
        if retailer.assigned_salesman_id:
            retailers_by_salesman[retailer.assigned_salesman_id].append(retailer)

    sku_meta = {item["code"]: item for item in SKUS}
    sku_codes = list(sku_by_code.keys())
    generated_orders = 0
    for offset in range(0, 180, 2):
        order_date = today - timedelta(days=offset)
        salesman_idx = offset % len(salesman_user_list)
        salesman_user = salesman_user_list[salesman_idx]
        salesman_emp = salesman_list[salesman_idx]
        candidates = retailers_by_salesman.get(salesman_emp.id) or retailer_list
        if not candidates:
            continue
        retailer = candidates[(offset + salesman_idx) % len(candidates)]
        warehouse = warehouses[salesman_idx % len(warehouses)]
        invoice_number = f"INV{order_date.strftime('%y%m%d')}{salesman_idx:02d}{offset % 9}"
        if db.query(Order).filter(Order.invoice_number == invoice_number).first():
            continue

        items = []
        items_count = 2 + (offset % 3)
        for item_idx in range(items_count):
            sku_code = sku_codes[(offset + item_idx) % len(sku_codes)]
            sku = sku_by_code.get(sku_code)
            if not sku:
                continue
            meta = sku_meta.get(sku_code, {})
            taxes = []
            if meta.get("cgst"):
                taxes.append(OrderItemTaxCreate(tax_type="CGST", rate=meta["cgst"]))
            if meta.get("sgst"):
                taxes.append(OrderItemTaxCreate(tax_type="SGST", rate=meta["sgst"]))
            if meta.get("igst"):
                taxes.append(OrderItemTaxCreate(tax_type="IGST", rate=meta["igst"]))
            quantity = 3 + (item_idx * 2) + (offset % 7)
            items.append(
                OrderItemCreate(
                    sku_id=sku.id,
                    quantity=quantity,
                    unit_price=meta.get("price", 100.0),
                    discount_amount=0,
                    taxes=taxes,
                )
            )

        if not items:
            continue

        topup_batches += ensure_stock_for_items(db, warehouse.id, items, sku_brand_map, today)
        order_payload = OrderCreate(retailer_id=retailer.id, warehouse_id=warehouse.id, items=items)
        try:
            order = create_outgoing_order(db, order_payload, salesman_user)
        except InsufficientStockError:
            continue

        update_order_status(db, order.id, StatusUpdate(status="CONFIRMED"))
        if offset % 6 != 0:
            update_order_status(db, order.id, StatusUpdate(status="DELIVERED"))
        order.invoice_number = invoice_number
        order.created_at = datetime.combine(order_date, datetime.min.time())
        db.commit()
        created_orders += 1
        generated_orders += 1

    delivered_orders = db.query(Order).filter(Order.status == OrderStatus.DELIVERED).order_by(Order.created_at.desc()).all()
    for idx, order in enumerate(delivered_orders[:80]):
        if order.payments:
            continue
        total = calculate_order_total(order)
        if total <= 0:
            continue
        if idx % 3 == 0:
            amount = total
        elif idx % 3 == 1:
            amount = total * 0.6
        else:
            continue
        payment = create_payment(
            db,
            order.id,
            PaymentCreate(
                amount=round(amount, 2),
                transaction_reference=f"PAY-{order.id}-{idx + 1}"
            )
        )
        if order.created_at:
            payment.created_at = order.created_at + timedelta(days=(idx % 5) + 1)
            db.commit()

    credit_note_count = min(20, len(delivered_orders))
    for idx, order in enumerate(delivered_orders[:credit_note_count]):
        if order.credit_notes:
            continue
        if not order.items:
            continue
        item = order.items[0]
        qty = max(int(item.quantity * 0.15), 1)
        qty = min(qty, int(item.quantity))
        if qty <= 0:
            continue
        credit_note = CreditNoteCreate(
            order_id=order.id,
            items=[CreditNoteItemCreate(sku_id=item.sku_id, quantity=qty, unit_price=item.unit_price)],
            restock=True,
        )
        try:
            note = create_credit_note(db, credit_note)
            if order.created_at:
                note.created_at = order.created_at + timedelta(days=(idx % 4) + 2)
                db.commit()
        except ValueError:
            continue

    updated_prices, added_taxes = backfill_order_items(db, sku_meta_by_id)

    db.close()

    print("Dummy data seeded from Excel (hardcoded + generated).")
    print(
        f"Warehouses: {len(warehouses)} | Retailers: {len(RETAILERS)} | "
        f"SKUs: {len(SKUS)} | Orders: {created_orders} | Generated: {generated_orders} | "
        f"Credit notes: {credit_note_count} | Top-ups: {topup_batches} | Item prices updated: {updated_prices} | "
        f"Taxes added: {added_taxes}"
    )
    print("Users (password: password):")
    print("  admin@ascend.com (ADMIN)")
    print("  accounts@ascend.com (ACCOUNTANT)")
    print("  warehouse@ascend.com (WAREHOUSE_MANAGER)")
    print("  Salesmen: one user per salesman name @ascend.local")


if __name__ == "__main__":
    main()
