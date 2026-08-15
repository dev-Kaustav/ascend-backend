from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models import Invoice, InvoiceLine, Order
from app.models.invoice import InvoiceImmutableError, DEFAULT_UQC
from app.models.enums import OrderStatus, InvoiceStatus, InvoiceType, SupplyType


def _make_order(db):
    order = Order(
        from_entity_type="BRAND",
        from_entity_id=1,
        to_entity_type="RETAILER",
        to_entity_id=1,
        status=OrderStatus.OUT_FOR_DELIVERY,
    )
    db.add(order)
    db.flush()
    return order


def _make_invoice(order, invoice_number="INV-0001"):
    return Invoice(
        invoice_number=invoice_number,
        invoice_date=datetime.now(timezone.utc),
        status=InvoiceStatus.ISSUED.value,
        order_id=order.id,
        invoice_type=InvoiceType.B2C.value,
        supply_type=SupplyType.REGULAR.value,
        reverse_charge=False,
        place_of_supply="Haryana",
        is_inter_state=False,
        supplier_legal_name="Ascend Foods",
        buyer_name="Test Retailer",
        taxable_value=Decimal("100.00"),
        discount_amount=Decimal("0.00"),
        cgst_amount=Decimal("9.00"),
        sgst_amount=Decimal("9.00"),
        igst_amount=Decimal("0.00"),
        cess_amount=Decimal("0.00"),
        total_tax_amount=Decimal("18.00"),
        grand_total=Decimal("118.00"),
    )


def _make_line(line_number=1):
    return InvoiceLine(
        line_number=line_number,
        description="Test SKU",
        hsn_code="12345678",
        quantity=10,
        uqc=DEFAULT_UQC,
        unit_rate=Decimal("11.80"),
        discount_amount=Decimal("0.00"),
        taxable_value=Decimal("100.00"),
        cgst_rate=Decimal("9.00"),
        cgst_amount=Decimal("9.00"),
        sgst_rate=Decimal("9.00"),
        sgst_amount=Decimal("9.00"),
        igst_rate=Decimal("0.00"),
        igst_amount=Decimal("0.00"),
        cess_rate=Decimal("0.00"),
        cess_amount=Decimal("0.00"),
        total_tax_amount=Decimal("18.00"),
        line_total=Decimal("118.00"),
    )


def _persist_invoice_with_line(db, invoice_number="INV-0001"):
    order = _make_order(db)
    invoice = _make_invoice(order, invoice_number=invoice_number)
    invoice.lines.append(_make_line())
    db.add(invoice)
    db.flush()
    return invoice


def test_invoice_and_lines_write_in_single_flush_succeeds(db):
    invoice = _persist_invoice_with_line(db, invoice_number="INV-WRITE-OK")
    assert invoice.id is not None
    assert invoice.lines[0].id is not None
    db.rollback()


def test_persisted_invoice_grand_total_cannot_be_mutated(db):
    invoice = _persist_invoice_with_line(db, invoice_number="INV-MUT-TOTAL")
    invoice.grand_total = Decimal("1.00")
    with pytest.raises(InvoiceImmutableError):
        db.flush()
    db.rollback()


def test_persisted_invoice_number_cannot_be_mutated(db):
    invoice = _persist_invoice_with_line(db, invoice_number="INV-MUT-NUMBER")
    invoice.invoice_number = "INV-CHANGED"
    with pytest.raises(InvoiceImmutableError):
        db.flush()
    db.rollback()


def test_persisted_invoice_status_cannot_be_mutated(db):
    invoice = _persist_invoice_with_line(db, invoice_number="INV-MUT-STATUS")
    invoice.status = "VOID"
    with pytest.raises(InvoiceImmutableError):
        db.flush()
    db.rollback()


def test_persisted_invoice_line_quantity_cannot_be_mutated(db):
    invoice = _persist_invoice_with_line(db, invoice_number="INV-MUT-LINE")
    invoice.lines[0].quantity = 999
    with pytest.raises(InvoiceImmutableError):
        db.flush()
    db.rollback()


def test_persisted_invoice_cannot_be_deleted(db):
    invoice = _persist_invoice_with_line(db, invoice_number="INV-DEL-INVOICE")
    db.delete(invoice)
    with pytest.raises(InvoiceImmutableError):
        db.flush()
    db.rollback()


def test_persisted_invoice_line_cannot_be_deleted(db):
    invoice = _persist_invoice_with_line(db, invoice_number="INV-DEL-LINE")
    line = invoice.lines[0]
    db.delete(line)
    with pytest.raises(InvoiceImmutableError):
        db.flush()
    db.rollback()
