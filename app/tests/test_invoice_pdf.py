"""Tests for app/services/invoice_pdf.py (INV-08 — this module had zero tests before
this plan) and the load-bearing evidence for INV-03: a PDF regenerated after the
underlying order has been edited is byte-identical to the one rendered at issue, and
matches the SHA-256 recorded at issue.
"""
import base64
import hashlib
import itertools
import re
import zlib
from decimal import Decimal

from app.models import Brand, CompanyProfile, Employee, Inventory, Invoice, InvoiceLine, SKU, SKUBatch, Retailer, User, Warehouse
from app.models.enums import EmployeeRole
from app.schemas.order import OrderCreate, OrderItemCreate, StatusUpdate
from app.core.security import create_access_token
from app.services.invoice_pdf import render_invoice_pdf, regenerate_invoice_pdf
from app.services.order import create_outgoing_order, update_order_status

_seed_counter = itertools.count()


def _dispatched_order(db, warehouse_state="Delhi", retailer_state="Delhi",
                       sgst_percent=9, cgst_percent=9, igst_percent=18, quantity=2, unit_price=100):
    """Seed a real Warehouse/Retailer/SKU/batch/inventory/user, create an order and
    drive it to OUT_FOR_DELIVERY. Returns (order, invoice)."""
    n = next(_seed_counter)
    brand = Brand(name=f"PDF Brand {n}")
    warehouse = Warehouse(name=f"PDF WH {n}", location="Loc", state=warehouse_state)
    retailer = Retailer(
        name=f"PDF Retailer {n}", state=retailer_state,
        address_line1="1 Retailer Lane", city="City", pincode=100001, gst_number=None,
    )
    db.add_all([brand, warehouse, retailer])
    db.commit()

    sku = SKU(
        name=f"PDF SKU {n}", brand_id=brand.id, hsn_code="12345678",
        sgst_percent=sgst_percent, cgst_percent=cgst_percent, igst_percent=igst_percent,
    )
    db.add(sku)
    db.commit()

    batch = SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=50, remaining_quantity=50)
    db.add(batch)
    inventory = Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=50)
    db.add(inventory)
    driver = Employee(name=f"PDF Driver {n}", email=f"pdf-driver-{n}@ascend.com", role=EmployeeRole.DRIVER)
    user = User(email=f"pdf-admin-{n}@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add_all([driver, user])
    db.commit()

    order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer.id,
            warehouse_id=warehouse.id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=quantity, unit_price=unit_price, discount_amount=0)],
        ),
        user,
    )
    order.delivery_driver_id = driver.id
    db.commit()
    update_order_status(db, order.id, StatusUpdate(status="READY_TO_SHIP"))
    update_order_status(db, order.id, StatusUpdate(status="OUT_FOR_DELIVERY"))
    db.refresh(order)
    return order, order.invoice


def _decoded_page_text(pdf_bytes: bytes) -> bytes:
    """Extract the (decompressed) page content stream text so a test can assert on
    what was actually rendered. ReportLab's default filter chain here is
    [ASCII85Decode, FlateDecode] with no explicit compression flag — decode both
    layers rather than weaken this to a raw-bytes search, which would silently pass
    against a differently-encoded stream."""
    out = b""
    for content in re.findall(rb"stream\r?\n(.*?)endstream", pdf_bytes, re.DOTALL):
        content = content.strip()
        if content.endswith(b"~>"):
            content = content[:-2]
        try:
            out += zlib.decompress(base64.a85decode(content, adobe=False))
        except Exception:
            # Non-content streams (fonts, etc.) that aren't ASCII85+Flate encoded —
            # not needed for text assertions, safe to skip.
            continue
    return out


def _bare_invoice() -> Invoice:
    """Build an Invoice + InvoiceLine purely in memory, never added to a Session —
    pins the property that makes issue-time digest capture possible at all."""
    from datetime import datetime, timezone

    invoice = Invoice(
        order_id=999,
        invoice_number="ASC900001",
        invoice_date=datetime.now(timezone.utc),
        status="ISSUED",
        invoice_type="B2C",
        supply_type="REGULAR",
        reverse_charge=False,
        place_of_supply="Delhi",
        is_inter_state=False,
        supplier_legal_name="Ascend Foods",
        supplier_gstin=None,
        supplier_state="Delhi",
        supplier_address="Warehouse Road",
        supplier_pincode="110001",
        buyer_name="Transient Buyer",
        buyer_gstin=None,
        buyer_state="Delhi",
        buyer_address="Buyer Road",
        buyer_pincode="110002",
        taxable_value=Decimal("100.00"),
        discount_amount=Decimal("0.00"),
        cgst_amount=Decimal("9.00"),
        sgst_amount=Decimal("9.00"),
        igst_amount=Decimal("0.00"),
        cess_amount=Decimal("0.00"),
        total_tax_amount=Decimal("18.00"),
        grand_total=Decimal("118.00"),
    )
    invoice.lines.append(InvoiceLine(
        line_number=1,
        sku_id=1,
        description="Transient SKU",
        hsn_code="12345678",
        quantity=2,
        uqc="PCS",
        unit_rate=Decimal("50.00"),
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
    ))
    return invoice


def test_regenerated_pdf_is_byte_identical_to_the_issue_time_render(db):
    order, invoice = _dispatched_order(db)

    bytes_a, _ = regenerate_invoice_pdf(db, invoice)
    bytes_a = bytes_a.getvalue()
    bytes_b, _ = regenerate_invoice_pdf(db, invoice)
    bytes_b = bytes_b.getvalue()

    assert bytes_a == bytes_b
    assert hashlib.sha256(bytes_a).hexdigest() == invoice.pdf_sha256


def test_editing_the_order_after_issue_does_not_change_the_pdf(db):
    """This is INV-03."""
    order, invoice = _dispatched_order(db)
    original_bytes, _ = regenerate_invoice_pdf(db, invoice)
    original_bytes = original_bytes.getvalue()

    item = order.items[0]
    original_unit_price = item.unit_price
    original_quantity = item.quantity
    original_discount = item.discount_amount

    # Mutate as broadly as the schema allows.
    item.unit_price = Decimal("999.00")
    item.quantity = 999
    item.discount_amount = Decimal("50.00")

    late_brand = Brand(name="Late Brand")
    db.add(late_brand)
    db.flush()
    sku2 = SKU(name="Late-added SKU", brand_id=late_brand.id)
    db.add(sku2)
    db.flush()
    from app.models import OrderItem, OrderItemTax
    new_item = OrderItem(order_id=order.id, sku_id=sku2.id, quantity=3, unit_price=Decimal("20.00"), discount_amount=Decimal("0.00"))
    db.add(new_item)
    db.flush()
    db.add(OrderItemTax(order_item_id=new_item.id, tax_type="CGST", rate=Decimal("9.00")))
    db.add(OrderItemTax(order_item_id=new_item.id, tax_type="SGST", rate=Decimal("9.00")))

    retailer = db.query(Retailer).filter(Retailer.id == order.to_entity_id).first()
    retailer.name = "Renamed Retailer Inc."
    retailer.gst_number = "27ABCDE1234F1Z5"

    company = db.query(CompanyProfile).order_by(CompanyProfile.id.asc()).first()
    if company is None:
        company = CompanyProfile(legal_name="Mutated Legal Name", invoice_prefix="ASC")
        db.add(company)
    else:
        company.legal_name = "Mutated Legal Name"

    db.commit()

    # Control: the mutations really landed.
    db.refresh(item)
    db.refresh(retailer)
    assert item.unit_price != original_unit_price
    assert item.quantity != original_quantity
    assert item.discount_amount != original_discount
    assert retailer.name == "Renamed Retailer Inc."
    assert retailer.gst_number == "27ABCDE1234F1Z5"

    regenerated_bytes, _ = regenerate_invoice_pdf(db, invoice)
    regenerated_bytes = regenerated_bytes.getvalue()

    assert regenerated_bytes == original_bytes
    assert hashlib.sha256(regenerated_bytes).hexdigest() == invoice.pdf_sha256


def test_pdf_renders_the_snapshot_values_not_the_live_ones(db):
    order, invoice = _dispatched_order(db)
    original_retailer_name = invoice.buyer_name
    original_unit_rate = str(invoice.lines[0].unit_rate)

    retailer = db.query(Retailer).filter(Retailer.id == order.to_entity_id).first()
    retailer.name = "Post-Issue Renamed Retailer"
    item = order.items[0]
    item.unit_price = Decimal("777.00")
    db.commit()

    regenerated_bytes, _ = regenerate_invoice_pdf(db, invoice)
    text = _decoded_page_text(regenerated_bytes.getvalue())

    assert original_retailer_name.encode() in text
    assert f"{Decimal(original_unit_rate):,.2f}".encode() in text
    assert b"Post-Issue Renamed Retailer" not in text
    assert b"777.00" not in text


def test_inter_state_invoice_renders_igst_and_intra_state_renders_cgst_sgst(db):
    inter_order, inter_invoice = _dispatched_order(db, warehouse_state="Delhi", retailer_state="Karnataka")
    intra_order, intra_invoice = _dispatched_order(db, warehouse_state="Delhi", retailer_state="Delhi")

    assert inter_invoice.is_inter_state is True
    assert intra_invoice.is_inter_state is False

    inter_bytes, _ = regenerate_invoice_pdf(db, inter_invoice)
    inter_text = _decoded_page_text(inter_bytes.getvalue())
    assert b"IGST" in inter_text
    assert b"SGST" not in inter_text
    assert b"CGST" not in inter_text

    intra_bytes, _ = regenerate_invoice_pdf(db, intra_invoice)
    intra_text = _decoded_page_text(intra_bytes.getvalue())
    assert b"SGST" in intra_text
    assert b"CGST" in intra_text
    assert b"IGST" not in intra_text


def test_zero_tax_invoice_renders(db):
    order, invoice = _dispatched_order(db, sgst_percent=None, cgst_percent=None, igst_percent=None)
    assert invoice.total_tax_amount == Decimal("0.00")

    pdf_bytes, filename = regenerate_invoice_pdf(db, invoice)
    data = pdf_bytes.getvalue()
    assert data.startswith(b"%PDF")
    assert len(data) > 500
    assert filename == f"{invoice.invoice_number}.pdf"


def test_render_works_on_a_transient_invoice():
    invoice = _bare_invoice()
    assert invoice.id is None

    pdf_bytes = render_invoice_pdf(invoice)
    data = pdf_bytes.getvalue()
    assert data.startswith(b"%PDF")
    assert invoice.id is None


def test_invoice_pdf_endpoint_404s_before_dispatch_and_200s_after(client, db):
    brand = Brand(name="Endpoint Brand")
    warehouse = Warehouse(name="Endpoint WH", location="Loc", state="Delhi")
    retailer = Retailer(name="Endpoint Retailer", state="Delhi")
    db.add_all([brand, warehouse, retailer])
    db.commit()
    sku = SKU(name="Endpoint SKU", brand_id=brand.id, sgst_percent=9, cgst_percent=9)
    db.add(sku)
    db.commit()
    batch = SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=10, remaining_quantity=10)
    db.add(batch)
    inventory = Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=10)
    db.add(inventory)
    driver = Employee(name="Endpoint Driver", email="endpoint-driver@ascend.com", role=EmployeeRole.DRIVER)
    admin = User(email="endpoint-admin@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add_all([driver, admin])
    db.commit()
    token = create_access_token({"user_id": admin.id, "role": EmployeeRole.ADMIN.value})

    order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer.id,
            warehouse_id=warehouse.id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=1, unit_price=100, discount_amount=0)],
        ),
        admin,
    )

    response = client.get(f"/orders/{order.id}/invoice.pdf", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404

    order.delivery_driver_id = driver.id
    db.commit()
    update_order_status(db, order.id, StatusUpdate(status="READY_TO_SHIP"))
    update_order_status(db, order.id, StatusUpdate(status="OUT_FOR_DELIVERY"))
    db.refresh(order)

    response = client.get(f"/orders/{order.id}/invoice.pdf", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert order.invoice.invoice_number in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")


def test_totals_printed_on_the_pdf_match_the_stored_invoice(db):
    order, invoice = _dispatched_order(db)
    pdf_bytes, _ = regenerate_invoice_pdf(db, invoice)
    text = _decoded_page_text(pdf_bytes.getvalue())
    grand_total_str = f"{invoice.grand_total:,.2f}".encode()
    assert grand_total_str in text
