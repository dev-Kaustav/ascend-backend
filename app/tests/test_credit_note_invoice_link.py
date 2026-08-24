"""Tests for INV-06: a credit note references the invoice it reverses, by foreign key,
by number and by date — the shape GSTR-1's CDNR table requires.
"""
import itertools

from app.models import Brand, CreditNote, Employee, Inventory, Invoice, Order, OrderItem, OrderItemTax, SKU, SKUBatch, Retailer, User, Warehouse
from app.models.enums import EmployeeRole, OrderStatus
from app.schemas.accounting import CreditNoteCreate, CreditNoteItemCreate
from app.schemas.order import OrderCreate, OrderItemCreate, StatusUpdate
from app.services.accounting import create_credit_note
from app.services.order import create_outgoing_order, update_order_status

_seed_counter = itertools.count()


def _seed_delivered_order(db, quantity=5, sgst_percent=9, cgst_percent=9):
    """Seed a real Warehouse/Retailer/SKU/driver, create an order, and drive it all the
    way to DELIVERED through real transitions — dispatch issues the invoice at
    OUT_FOR_DELIVERY (plan 02-03), so a DELIVERED order reached this way always has one.
    """
    n = next(_seed_counter)
    brand = Brand(name=f"CN Brand {n}")
    warehouse = Warehouse(name=f"CN WH {n}", location="Delhi", state="Delhi")
    retailer = Retailer(name=f"CN Retailer {n}", state="Delhi")
    db.add_all([brand, warehouse, retailer])
    db.commit()

    sku = SKU(name=f"CN SKU {n}", brand_id=brand.id, hsn_code="12345678",
              sgst_percent=sgst_percent, cgst_percent=cgst_percent, igst_percent=18)
    db.add(sku)
    db.commit()

    batch = SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=50, remaining_quantity=50)
    db.add(batch)
    inventory = Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=50)
    db.add(inventory)
    driver = Employee(name=f"CN Driver {n}", email=f"cn-driver-{n}@ascend.com", role=EmployeeRole.DRIVER)
    user = User(email=f"cn-admin-{n}@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add_all([driver, user])
    db.commit()

    order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer.id,
            warehouse_id=warehouse.id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=quantity, unit_price=100, discount_amount=0)],
        ),
        user,
    )
    order.delivery_driver_id = driver.id
    db.commit()
    update_order_status(db, order.id, StatusUpdate(status="READY_TO_SHIP"), user)
    update_order_status(db, order.id, StatusUpdate(status="OUT_FOR_DELIVERY"), user)
    update_order_status(db, order.id, StatusUpdate(status="DELIVERED"), user)
    db.refresh(order)
    return order, sku.id


def _order_with_no_invoice(db, quantity=2):
    """The regression guard for test_accounting.py::test_credit_note_quantity_limits —
    an order constructed directly at DELIVERED, bypassing the real transitions, so it
    never passes through OUT_FOR_DELIVERY and never gets an invoice."""
    brand = Brand(name="No-Invoice Brand")
    db.add(brand)
    db.flush()
    sku = SKU(name="No-Invoice SKU", brand_id=brand.id)
    db.add(sku)
    db.flush()
    order = Order(
        from_entity_type="WAREHOUSE",
        from_entity_id=1,
        to_entity_type="RETAILER",
        to_entity_id=1,
        status=OrderStatus.DELIVERED,
    )
    db.add(order)
    db.flush()
    item = OrderItem(order_id=order.id, sku_id=sku.id, quantity=quantity, unit_price=100, discount_amount=0)
    db.add(item)
    db.flush()
    tax = OrderItemTax(order_item_id=item.id, tax_type="GST", rate=0)
    db.add(tax)
    db.commit()
    return order, sku.id


def test_credit_note_references_the_invoice_it_reverses(db):
    order, sku_id = _seed_delivered_order(db)
    invoice = order.invoice
    assert invoice is not None

    cn = create_credit_note(
        db,
        CreditNoteCreate(order_id=order.id, items=[CreditNoteItemCreate(sku_id=sku_id, quantity=1, unit_price=100)]),
    )

    assert cn.invoice_id == invoice.id
    assert cn.original_invoice_number == invoice.invoice_number
    assert cn.original_invoice_date == invoice.invoice_date
    assert cn.note_type == "C"


def test_credit_note_reference_survives_a_reread(db):
    order, sku_id = _seed_delivered_order(db)
    invoice = order.invoice
    cn = create_credit_note(
        db,
        CreditNoteCreate(order_id=order.id, items=[CreditNoteItemCreate(sku_id=sku_id, quantity=1, unit_price=100)]),
    )
    cn_id = cn.id
    invoice_id = invoice.id

    db.expire_all()

    reread = db.query(CreditNote).filter(CreditNote.id == cn_id).first()
    assert reread.original_invoice_number == invoice.invoice_number
    assert reread.original_invoice_date == invoice.invoice_date
    assert reread.invoice.id == invoice_id


def test_credit_note_on_an_order_with_no_invoice_leaves_the_reference_null(db):
    order, sku_id = _order_with_no_invoice(db, quantity=2)
    assert order.invoice is None

    cn = create_credit_note(
        db,
        CreditNoteCreate(order_id=order.id, items=[CreditNoteItemCreate(sku_id=sku_id, quantity=1, unit_price=100)]),
    )

    assert cn.invoice_id is None
    assert cn.original_invoice_number is None
    assert cn.original_invoice_date is None


def test_creating_a_credit_note_does_not_modify_the_invoice(db):
    order, sku_id = _seed_delivered_order(db)
    invoice_id = order.invoice.id
    sha_before = order.invoice.pdf_sha256
    grand_total_before = order.invoice.grand_total

    create_credit_note(
        db,
        CreditNoteCreate(order_id=order.id, items=[CreditNoteItemCreate(sku_id=sku_id, quantity=1, unit_price=100)]),
    )

    db.expire_all()
    invoice_after = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    assert invoice_after.pdf_sha256 == sha_before
    assert invoice_after.grand_total == grand_total_before


def test_cancelling_after_dispatch_leaves_the_invoice_and_a_credit_note_is_the_reversal(db):
    # D-01: "cancelling after dispatch is a credit-note case, not an invoice deletion."
    # DELIVERED has no CANCELLED transition in the current state machine (only
    # RETURNED — order-status hardening is Phase 3's scope, not this plan's), and
    # create_credit_note requires DELIVERED. So the reversal *is* the credit note
    # created against the delivered order — there is no separate CANCELLED status flip
    # in this codebase's real lifecycle. That is exactly the property this test proves:
    # the invoice is untouched, and the credit note is the trace of the reversal.
    order, sku_id = _seed_delivered_order(db, quantity=3)
    invoice = order.invoice
    invoice_id = invoice.id
    sha_before = invoice.pdf_sha256
    grand_total_before = invoice.grand_total

    cn = create_credit_note(
        db,
        CreditNoteCreate(order_id=order.id, items=[CreditNoteItemCreate(sku_id=sku_id, quantity=3, unit_price=100)]),
    )

    db.expire_all()
    invoice_after = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    assert invoice_after is not None
    assert invoice_after.pdf_sha256 == sha_before
    assert invoice_after.grand_total == grand_total_before
    assert cn.invoice_id == invoice_id


def test_every_cdnr_field_is_reachable_from_the_credit_note(db):
    order, sku_id = _seed_delivered_order(db)
    cn = create_credit_note(
        db,
        CreditNoteCreate(order_id=order.id, items=[CreditNoteItemCreate(sku_id=sku_id, quantity=1, unit_price=100)]),
    )

    # Through the invoice FK — reachable without a second query against orders:
    assert cn.invoice is not None
    _ = cn.invoice.buyer_gstin  # recipient GSTIN
    assert cn.invoice.place_of_supply is not None
    assert cn.invoice.reverse_charge is not None
    assert cn.invoice.supply_type is not None
    # As columns on the credit note itself:
    assert cn.original_invoice_number is not None
    assert cn.original_invoice_date is not None
    assert cn.credit_note_number is not None
    assert cn.note_date is not None
    assert cn.note_type == "C"
