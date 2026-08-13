from sqlalchemy import Float, Numeric, Integer

from app.models import (
    SKU,
    OrderItem,
    OrderItemTax,
    Account,
    CreditNoteItem,
    Inventory,
    SKUBatch,
    OrderItemBatch,
    InventoryTransaction,
    Retailer,
    Warehouse,
    CompanyProfile,
)
from app.models.enums import INDIAN_STATES

MONEY_COLUMNS = [
    (SKU, "mrp"),
    (SKU, "rate"),
    (SKU, "amount"),
    (SKU, "sgst_percent"),
    (SKU, "cgst_percent"),
    (SKU, "igst_percent"),
    (SKU, "sgst_amount"),
    (SKU, "cgst_amount"),
    (SKU, "igst_amount"),
    (SKU, "distributor_landing_price"),
    (SKU, "discount_amount"),
    (SKU, "discount_percent"),
    (OrderItem, "unit_price"),
    (OrderItem, "discount_amount"),
    (OrderItemTax, "rate"),
    (Account, "amount"),
    (CreditNoteItem, "unit_price"),
]

QUANTITY_COLUMNS = [
    (Inventory, "total_quantity"),
    (Inventory, "reserved_quantity"),
    (SKUBatch, "quantity_received"),
    (SKUBatch, "remaining_quantity"),
    (SKUBatch, "reserved_quantity"),
    (OrderItem, "quantity"),
    (CreditNoteItem, "quantity"),
    (OrderItemBatch, "quantity"),
    (InventoryTransaction, "quantity"),
]

STATE_TABLES = [Retailer, Warehouse, CompanyProfile]


def test_money_columns_are_decimal_12_2():
    for model, column_name in MONEY_COLUMNS:
        t = model.__table__.c[column_name].type
        assert isinstance(t, Numeric) and not isinstance(t, Float), (model.__name__, column_name)
        assert (t.precision, t.scale) == (12, 2), (model.__name__, column_name)


def test_quantity_columns_are_integer():
    for model, column_name in QUANTITY_COLUMNS:
        t = model.__table__.c[column_name].type
        assert isinstance(t, Integer), (model.__name__, column_name)


def test_state_columns_have_check_constraint():
    assert len(INDIAN_STATES) == 36
    for model in STATE_TABLES:
        constraint_names = {c.name for c in model.__table__.constraints}
        expected = f"ck_{model.__tablename__}_state"
        assert expected in constraint_names, (model.__name__, constraint_names)


def test_continuous_columns_remain_float():
    # Pins the deliberate exclusions: these are genuinely continuous physical
    # measurements (dimensions, weight, GPS coordinates), not money or
    # saleable quantity, and must not be "finished" into Decimal/Integer by a
    # future reader.
    for column_name in ("weight", "length_cm", "width_cm", "height_cm"):
        t = SKU.__table__.c[column_name].type
        assert isinstance(t, Float), column_name

    for column_name in ("latitude", "longitude"):
        t = Retailer.__table__.c[column_name].type
        assert isinstance(t, Float), column_name
