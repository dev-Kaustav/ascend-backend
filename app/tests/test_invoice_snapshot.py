from datetime import datetime
from decimal import Decimal

from app.models import Brand, Warehouse, Retailer, SKU, SKUBatch, Inventory, User, Invoice
from app.models.enums import EmployeeRole
from app.schemas.order import OrderCreate, OrderItemCreate
from app.services.order import create_outgoing_order
from app.services.finance import calculate_order_item_totals
from app.services.invoice import issue_invoice_for_order

TWO_PLACES = Decimal("0.01")


def _seed_entities(db, *, email, warehouse_state="Haryana", retailer_state="Haryana", retailer_gstin=None):
    brand = Brand(name="Brand")
    warehouse = Warehouse(name="WH", location=warehouse_state, state=warehouse_state)
    retailer = Retailer(
        name="Retailer",
        state=retailer_state,
        gst_number=retailer_gstin,
        address_line1="123 Market St",
        city="City",
        pincode=110001,
    )
    user = User(email=email, password_hash="x", role=EmployeeRole.ADMIN)
    db.add_all([brand, warehouse, retailer, user])
    db.flush()
    return brand, warehouse, retailer, user


def _seed_sku(db, brand, warehouse, *, name="SKU", hsn_code="1234", sgst_percent=None, cgst_percent=None, igst_percent=None, stock=100):
    sku = SKU(
        name=name,
        brand_id=brand.id,
        hsn_code=hsn_code,
        sgst_percent=sgst_percent,
        cgst_percent=cgst_percent,
        igst_percent=igst_percent,
    )
    db.add(sku)
    db.flush()
    db.add_all([
        SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=stock, remaining_quantity=stock),
        Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=stock),
    ])
    db.commit()
    return sku


def _place_order(db, warehouse, retailer, user, items):
    return create_outgoing_order(
        db,
        OrderCreate(retailer_id=retailer.id, warehouse_id=warehouse.id, items=items),
        user,
    )


def test_invoice_snapshot_populates_every_inv02_field(db):
    brand, warehouse, retailer, user = _seed_entities(db, email="snapshot@ascend.com")
    sku1 = _seed_sku(db, brand, warehouse, name="SKU One", hsn_code="1111", sgst_percent=2.5, cgst_percent=2.5)
    sku2 = _seed_sku(db, brand, warehouse, name="SKU Two", hsn_code="2222", sgst_percent=6, cgst_percent=6)

    order = _place_order(db, warehouse, retailer, user, [
        OrderItemCreate(sku_id=sku1.id, quantity=2, unit_price=100, discount_amount=0),
        OrderItemCreate(sku_id=sku2.id, quantity=3, unit_price=50, discount_amount=0),
    ])

    invoice = issue_invoice_for_order(db, order)

    assert len(invoice.lines) == 2
    line1, line2 = invoice.lines
    assert line1.description == "SKU One"
    assert line1.hsn_code == "1111"
    assert line1.quantity == 2
    assert line1.uqc == "PCS"
    assert line1.unit_rate == Decimal("100.00")
    assert line1.taxable_value == Decimal("190.00")
    assert line1.line_total == Decimal("200.00")

    assert line2.description == "SKU Two"
    assert line2.hsn_code == "2222"
    assert line2.quantity == 3
    assert line2.uqc == "PCS"
    assert line2.unit_rate == Decimal("50.00")
    assert line2.taxable_value == Decimal("132.00")
    assert line2.line_total == Decimal("150.00")


def test_invoice_line_tax_components_sum_to_the_single_computed_total(db):
    brand, warehouse, retailer, user = _seed_entities(db, email="components@ascend.com")
    sku = _seed_sku(db, brand, warehouse, sgst_percent=2.5, cgst_percent=2.5)

    order = _place_order(db, warehouse, retailer, user, [
        OrderItemCreate(sku_id=sku.id, quantity=2, unit_price=100, discount_amount=0),
    ])
    invoice = issue_invoice_for_order(db, order)

    for line, item in zip(invoice.lines, order.items):
        expected_gst_amount = calculate_order_item_totals(item)["gst_amount"]
        assert line.total_tax_amount == expected_gst_amount
        assert line.cgst_amount + line.sgst_amount + line.igst_amount + line.cess_amount == line.total_tax_amount


def test_invoice_line_taxable_plus_tax_equals_line_total(db):
    brand, warehouse, retailer, user = _seed_entities(db, email="rollup@ascend.com")
    sku1 = _seed_sku(db, brand, warehouse, name="SKU One", sgst_percent=2.5, cgst_percent=2.5)
    sku2 = _seed_sku(db, brand, warehouse, name="SKU Two", sgst_percent=6, cgst_percent=6)

    order = _place_order(db, warehouse, retailer, user, [
        OrderItemCreate(sku_id=sku1.id, quantity=2, unit_price=100, discount_amount=0),
        OrderItemCreate(sku_id=sku2.id, quantity=3, unit_price=50, discount_amount=0),
    ])
    invoice = issue_invoice_for_order(db, order)

    for line in invoice.lines:
        assert line.taxable_value + line.total_tax_amount == line.line_total

    assert invoice.taxable_value + invoice.total_tax_amount == invoice.grand_total
    assert invoice.taxable_value == sum((line.taxable_value for line in invoice.lines), Decimal("0"))
    assert invoice.total_tax_amount == sum((line.total_tax_amount for line in invoice.lines), Decimal("0"))
    assert invoice.grand_total == sum((line.line_total for line in invoice.lines), Decimal("0"))


def test_intra_state_invoice_splits_cgst_sgst_and_inter_state_uses_igst(db):
    brand = Brand(name="Brand")
    warehouse = Warehouse(name="Delhi WH", location="Delhi", state="Delhi")
    retailer_same = Retailer(name="Same State", state="Delhi", address_line1="A1", city="Delhi", pincode=110001)
    retailer_other = Retailer(name="Other State", state="Karnataka", address_line1="A2", city="Bangalore", pincode=560001)
    user = User(email="interstate@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add_all([brand, warehouse, retailer_same, retailer_other, user])
    db.flush()
    sku = SKU(name="Tax SKU", brand_id=brand.id, hsn_code="9999", sgst_percent=2.5, cgst_percent=2.5, igst_percent=5)
    db.add(sku)
    db.flush()
    db.add_all([
        SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=10, remaining_quantity=10),
        Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=10),
    ])
    db.commit()

    same_order = _place_order(db, warehouse, retailer_same, user, [
        OrderItemCreate(sku_id=sku.id, quantity=1, unit_price=100, discount_amount=0),
    ])
    same_invoice = issue_invoice_for_order(db, same_order)
    same_line = same_invoice.lines[0]
    assert same_line.cgst_rate > 0
    assert same_line.sgst_rate > 0
    assert same_line.igst_rate == 0
    assert same_invoice.is_inter_state is False
    assert same_invoice.place_of_supply == "Delhi"

    other_order = _place_order(db, warehouse, retailer_other, user, [
        OrderItemCreate(sku_id=sku.id, quantity=1, unit_price=100, discount_amount=0),
    ])
    other_invoice = issue_invoice_for_order(db, other_order)
    other_line = other_invoice.lines[0]
    assert other_line.igst_rate > 0
    assert other_line.cgst_rate == 0
    assert other_line.sgst_rate == 0
    assert other_invoice.is_inter_state is True
    assert other_invoice.place_of_supply == "Karnataka"


def test_zero_tax_order_produces_zero_tax_invoice_without_raising(db):
    brand, warehouse, retailer, user = _seed_entities(db, email="zerotax@ascend.com")
    sku = _seed_sku(db, brand, warehouse)  # no GST percentages set

    order = _place_order(db, warehouse, retailer, user, [
        OrderItemCreate(sku_id=sku.id, quantity=2, unit_price=50, discount_amount=0),
    ])
    assert order.items[0].taxes == []

    invoice = issue_invoice_for_order(db, order)
    line = invoice.lines[0]
    assert line.cgst_amount == Decimal("0.00")
    assert line.sgst_amount == Decimal("0.00")
    assert line.igst_amount == Decimal("0.00")
    assert line.cess_amount == Decimal("0.00")
    assert line.total_tax_amount == Decimal("0.00")
    assert line.taxable_value == line.line_total


def test_invoice_type_is_b2c_without_buyer_gstin_and_b2b_with_one(db):
    brand, warehouse, retailer_no_gstin, user = _seed_entities(db, email="b2c@ascend.com", retailer_gstin=None)
    sku = _seed_sku(db, brand, warehouse, sgst_percent=2.5, cgst_percent=2.5)
    order_b2c = _place_order(db, warehouse, retailer_no_gstin, user, [
        OrderItemCreate(sku_id=sku.id, quantity=1, unit_price=100, discount_amount=0),
    ])
    invoice_b2c = issue_invoice_for_order(db, order_b2c)
    assert invoice_b2c.invoice_type == "B2C"

    retailer_with_gstin = Retailer(name="B2B Retailer", state="Haryana", gst_number="27AAAAA0000A1Z5", address_line1="A", city="City", pincode=110002)
    db.add(retailer_with_gstin)
    db.commit()
    order_b2b = _place_order(db, warehouse, retailer_with_gstin, user, [
        OrderItemCreate(sku_id=sku.id, quantity=1, unit_price=100, discount_amount=0),
    ])
    invoice_b2b = issue_invoice_for_order(db, order_b2b)
    assert invoice_b2b.invoice_type == "B2B"
    assert invoice_b2b.buyer_gstin == "27AAAAA0000A1Z5"


def test_issue_is_idempotent(db):
    brand, warehouse, retailer, user = _seed_entities(db, email="idempotent@ascend.com")
    sku = _seed_sku(db, brand, warehouse, sgst_percent=2.5, cgst_percent=2.5)
    order = _place_order(db, warehouse, retailer, user, [
        OrderItemCreate(sku_id=sku.id, quantity=1, unit_price=100, discount_amount=0),
    ])

    first = issue_invoice_for_order(db, order)
    second = issue_invoice_for_order(db, order)

    assert first.id == second.id
    assert db.query(Invoice).count() == 1
    assert first.invoice_number == second.invoice_number


def test_external_invoice_number_is_accepted_and_leaves_serial_null(db):
    brand, warehouse, retailer, user = _seed_entities(db, email="external@ascend.com")
    sku = _seed_sku(db, brand, warehouse, sgst_percent=2.5, cgst_percent=2.5)
    order = _place_order(db, warehouse, retailer, user, [
        OrderItemCreate(sku_id=sku.id, quantity=1, unit_price=100, discount_amount=0),
    ])
    external_date = datetime(2025, 4, 1, 10, 30, 0)

    invoice = issue_invoice_for_order(db, order, invoice_number="CA-000123", invoice_date=external_date)

    assert invoice.invoice_number == "CA-000123"
    assert invoice.invoice_date == external_date
    assert invoice.invoice_serial is None


def test_invoice_line_order_is_stable(db):
    brand, warehouse, retailer, user = _seed_entities(db, email="stableorder@ascend.com")
    sku1 = _seed_sku(db, brand, warehouse, name="A", stock=10)
    sku2 = _seed_sku(db, brand, warehouse, name="B", stock=10)
    sku3 = _seed_sku(db, brand, warehouse, name="C", stock=10)

    order = _place_order(db, warehouse, retailer, user, [
        OrderItemCreate(sku_id=sku1.id, quantity=1, unit_price=10, discount_amount=0),
        OrderItemCreate(sku_id=sku2.id, quantity=1, unit_price=10, discount_amount=0),
        OrderItemCreate(sku_id=sku3.id, quantity=1, unit_price=10, discount_amount=0),
    ])
    expected_item_id_order = [item.id for item in sorted(order.items, key=lambda i: i.id)]

    invoice = issue_invoice_for_order(db, order)
    assert [line.line_number for line in invoice.lines] == [1, 2, 3]

    invoice_id = invoice.id
    db.flush()
    reread = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    assert [line.sku_id for line in reread.lines] == [
        next(item.sku_id for item in order.items if item.id == item_id) for item_id in expected_item_id_order
    ]
    assert [line.line_number for line in reread.lines] == [1, 2, 3]


def test_every_money_field_on_the_invoice_is_a_decimal(db):
    brand, warehouse, retailer, user = _seed_entities(db, email="decimals@ascend.com")
    sku = _seed_sku(db, brand, warehouse, sgst_percent=2.5, cgst_percent=2.5)
    order = _place_order(db, warehouse, retailer, user, [
        OrderItemCreate(sku_id=sku.id, quantity=1, unit_price=100, discount_amount=0),
    ])
    invoice = issue_invoice_for_order(db, order)
    db.flush()
    db.refresh(invoice)

    invoice_money_fields = [
        "taxable_value", "discount_amount", "cgst_amount", "sgst_amount",
        "igst_amount", "cess_amount", "total_tax_amount", "grand_total",
    ]
    for field in invoice_money_fields:
        assert isinstance(getattr(invoice, field), Decimal), field

    line_money_fields = [
        "unit_rate", "discount_amount", "taxable_value", "cgst_rate", "cgst_amount",
        "sgst_rate", "sgst_amount", "igst_rate", "igst_amount", "cess_rate",
        "cess_amount", "total_tax_amount", "line_total",
    ]
    for line in invoice.lines:
        db.refresh(line)
        for field in line_money_fields:
            assert isinstance(getattr(line, field), Decimal), field
