"""Store invoices (08-10): the chosen delivery address prints as Ship To, a retailer downloads
its own invoice, and GST follows the registered shop state (D-29, D-35, PORT-06).
"""
import hashlib
import itertools
from datetime import datetime, timezone
from decimal import Decimal

from app.models import CompanyProfile, Employee, Invoice, InvoiceLine, Order, User
from app.models.enums import EmployeeRole
from app.schemas.order import StatusUpdate
from app.services.invoice_pdf import regenerate_invoice_pdf, render_invoice_pdf
from app.services.order import update_order_status
from app.tests.store_helpers import (
    auth_headers,
    make_brand,
    make_category,
    make_storefront,
    make_user,
    ready_retailer,
    stocked_sku,
)
from app.tests.test_invoice_pdf import _decoded_page_text

_n = itertools.count(1)

# sha256 of the PDF the renderer produced for _pinned_invoice() BEFORE 08-10 touched
# invoice_pdf.py. An invoice with NULL ship_to_* must keep rendering to exactly these bytes
# (research Pitfall 13: issued invoices are byte-pinned legal records).
PRE_CHANGE_NULL_SHIP_TO_DIGEST = "e44cea4524438d37ffc1c7387f420304d6b77871115d06ffa1f868a78e8a3148"


def _pinned_invoice(**overrides) -> Invoice:
    invoice = Invoice(
        order_id=999,
        invoice_number="ASC/PIN/0001",
        invoice_date=datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc),
        status="ISSUED",
        invoice_type="B2B",
        supply_type="REGULAR",
        reverse_charge=False,
        place_of_supply="Delhi",
        is_inter_state=False,
        supplier_legal_name="Ascend Foods",
        supplier_gstin="07AAAAA0000A1Z5",
        supplier_state="Delhi",
        supplier_address="Warehouse Road",
        supplier_pincode="110001",
        buyer_name="Pinned Buyer",
        buyer_gstin="07BBBBB1111B1Z5",
        buyer_state="Delhi",
        buyer_address="Buyer Road, Block B",
        buyer_pincode="110002",
        taxable_value=Decimal("100.00"),
        discount_amount=Decimal("0.00"),
        cgst_amount=Decimal("6.00"),
        sgst_amount=Decimal("6.00"),
        igst_amount=Decimal("0.00"),
        cess_amount=Decimal("0.00"),
        total_tax_amount=Decimal("12.00"),
        grand_total=Decimal("112.00"),
        **overrides,
    )
    invoice.lines.append(
        InvoiceLine(
            line_number=1,
            sku_id=1,
            description="Pinned SKU",
            hsn_code="2008",
            quantity=2,
            uqc="PCS",
            unit_rate=Decimal("50.00"),
            discount_amount=Decimal("0.00"),
            taxable_value=Decimal("100.00"),
            cgst_rate=Decimal("6.00"),
            cgst_amount=Decimal("6.00"),
            sgst_rate=Decimal("6.00"),
            sgst_amount=Decimal("6.00"),
            igst_rate=Decimal("0.00"),
            igst_amount=Decimal("0.00"),
            cess_rate=Decimal("0.00"),
            cess_amount=Decimal("0.00"),
            total_tax_amount=Decimal("12.00"),
            line_total=Decimal("112.00"),
        )
    )
    return invoice


def _store(db, *, warehouse_state="Delhi"):
    warehouse = make_storefront(db, state=warehouse_state)
    sku = stocked_sku(
        db,
        warehouse=warehouse,
        brand=make_brand(db),
        category=make_category(db, "Chips"),
        name="Masala Chips",
        code="CHN-MSL-23g",
        mrp="22.5",
        amount="18.0",
        gst_rate=Decimal("12"),
        qty=50,
    )
    return warehouse, sku


def _order_via_api(client, user, sku, address, quantity=2):
    response = client.post(
        "/store/orders",
        json={"items": [{"sku_id": sku.id, "quantity": quantity}], "address_id": address.id},
        headers=auth_headers(user),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _dispatch(db, order_id):
    """READY_TO_SHIP -> OUT_FOR_DELIVERY as ops would; the invoice is issued at the second step."""
    n = next(_n)
    driver = Employee(name=f"Store Driver {n}", email=f"store-driver-{n}@ascend.com", role=EmployeeRole.DRIVER)
    admin = User(email=f"store-inv-admin-{n}@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add_all([driver, admin])
    db.commit()
    order = db.query(Order).filter(Order.id == order_id).one()
    order.delivery_driver_id = driver.id
    db.commit()
    update_order_status(db, order_id, StatusUpdate(status="READY_TO_SHIP"), admin)
    update_order_status(db, order_id, StatusUpdate(status="OUT_FOR_DELIVERY"), admin)
    db.refresh(order)
    return order.invoice


def _register(db, retailer, address, *, same):
    """Give the registered shop an address; identical to the saved one when `same`."""
    if same:
        retailer.address_line1 = address.line1
        retailer.address_line2 = address.line2
        retailer.state = address.state
        retailer.pincode = address.pincode
    else:
        retailer.address_line1 = "Registered Lane 1"
        retailer.address_line2 = None
        retailer.pincode = address.pincode
    db.commit()


def test_null_ship_to_invoice_renders_to_the_pre_change_bytes(db):
    company = CompanyProfile(legal_name="Ascend Foods", invoice_prefix="ASC")
    pdf = render_invoice_pdf(_pinned_invoice(), company).getvalue()
    assert hashlib.sha256(pdf).hexdigest() == PRE_CHANGE_NULL_SHIP_TO_DIGEST


def test_a_different_delivery_address_is_snapshotted_and_printed_as_ship_to(client, db):
    _, sku = _store(db)
    user, retailer, address = ready_retailer(db)
    _register(db, retailer, address, same=False)

    invoice = _dispatch(db, _order_via_api(client, user, sku, address))

    assert address.line1 in invoice.ship_to_address
    assert "Block B" in invoice.ship_to_address and "Near the temple" in invoice.ship_to_address
    assert invoice.ship_to_state == "Delhi"
    assert invoice.ship_to_pincode == "110001"
    # Bill To is still the Retailer record.
    assert invoice.buyer_address == "Registered Lane 1"

    pdf, _ = regenerate_invoice_pdf(db, invoice)
    pdf = pdf.getvalue()
    assert hashlib.sha256(pdf).hexdigest() == invoice.pdf_sha256
    text = _decoded_page_text(pdf)
    assert address.line1.encode() in text
    assert b"Registered Lane 1" in text


def test_a_delivery_address_equal_to_the_registered_one_leaves_ship_to_null(client, db):
    _, sku = _store(db)
    user, retailer, address = ready_retailer(db)
    _register(db, retailer, address, same=True)

    invoice = _dispatch(db, _order_via_api(client, user, sku, address))

    assert invoice.ship_to_address is None
    assert invoice.ship_to_state is None
    assert invoice.ship_to_pincode is None


def test_owner_downloads_the_invoice_of_a_dispatched_order(client, db):
    _, sku = _store(db)
    user, retailer, address = ready_retailer(db)
    order_id = _order_via_api(client, user, sku, address)
    invoice = _dispatch(db, order_id)

    response = client.get(f"/store/orders/{order_id}/invoice.pdf", headers=auth_headers(user))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert invoice.invoice_number in response.headers["content-disposition"]
    assert hashlib.sha256(response.content).hexdigest() == invoice.pdf_sha256


def test_no_invoice_before_dispatch_is_a_404(client, db):
    _, sku = _store(db)
    user, retailer, address = ready_retailer(db)
    order_id = _order_via_api(client, user, sku, address)

    response = client.get(f"/store/orders/{order_id}/invoice.pdf", headers=auth_headers(user))

    assert response.status_code == 404
    assert response.json()["detail"] == "No invoice yet"


def test_another_retailers_or_a_missing_order_is_a_404_not_a_403(client, db):
    _, sku = _store(db)
    user, retailer, address = ready_retailer(db)
    other_user, _, _ = ready_retailer(db)
    order_id = _order_via_api(client, user, sku, address)
    _dispatch(db, order_id)

    foreign = client.get(f"/store/orders/{order_id}/invoice.pdf", headers=auth_headers(other_user))
    missing = client.get("/store/orders/999999/invoice.pdf", headers=auth_headers(other_user))

    assert foreign.status_code == 404 and foreign.json()["detail"] == "Order not found"
    assert missing.status_code == 404 and missing.json()["detail"] == "Order not found"


def test_staff_tokens_cannot_use_the_store_invoice_route(client, db):
    _, sku = _store(db)
    user, retailer, address = ready_retailer(db)
    order_id = _order_via_api(client, user, sku, address)
    _dispatch(db, order_id)
    admin = make_user(db, EmployeeRole.ADMIN)

    response = client.get(f"/store/orders/{order_id}/invoice.pdf", headers=auth_headers(admin))

    assert response.status_code == 403
