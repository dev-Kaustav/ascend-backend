"""Store invoices (08-10): the chosen delivery address prints as Ship To, a retailer downloads
its own invoice, and GST follows the registered shop state (D-29, D-35, PORT-06).
"""
import hashlib
import itertools
from datetime import datetime, timezone
from decimal import Decimal

from app.models import (
    CompanyProfile,
    Employee,
    Invoice,
    InvoiceLine,
    Order,
    OrderItem,
    OrderItemTax,
    Retailer,
    RetailerAddress,
    User,
)
from app.models.enums import EmployeeRole
from app.schemas.order import StatusUpdate
from app.services.invoice_pdf import regenerate_invoice_pdf, render_invoice_pdf
from app.schemas.order import OrderCreate, OrderItemCreate
from app.services import order as order_service
from app.services.order import create_outgoing_order, update_order_status
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


# --- D-35: GST follows the registered shop state, never the chosen delivery address ------------


def _address_in(db, retailer, state, *, line1="9 Elsewhere Marg"):
    address = RetailerAddress(
        retailer_id=retailer.id,
        label="Other",
        line1=line1,
        city="Gurugram" if state == "Haryana" else "Delhi",
        state=state,
        pincode=122001 if state == "Haryana" else 110002,
        latitude=28.45,
        longitude=77.03,
    )
    db.add(address)
    db.commit()
    return address


def _tax_rows(db, order_id):
    return sorted(
        (tax.tax_type, tax.rate)
        for tax in db.query(OrderItemTax)
        .join(OrderItem, OrderItem.id == OrderItemTax.order_item_id)
        .filter(OrderItem.order_id == order_id)
        .all()
    )


def test_delhi_shop_with_a_haryana_delivery_address_stays_cgst_sgst(client, db):
    _, sku = _store(db, warehouse_state="Delhi")
    user, retailer, _ = ready_retailer(db, warehouse_state="Delhi")
    haryana = _address_in(db, retailer, "Haryana")

    order_id = _order_via_api(client, user, sku, haryana)
    invoice = _dispatch(db, order_id)

    assert _tax_rows(db, order_id) == [("CGST", Decimal("6.00")), ("SGST", Decimal("6.00"))]
    assert invoice.place_of_supply == "Delhi"
    assert invoice.is_inter_state is False
    assert invoice.igst_amount == Decimal("0.00") and invoice.cgst_amount > 0
    # The chosen delivery state still prints as Ship To.
    assert invoice.ship_to_state == "Haryana"
    assert invoice.ship_to_pincode == "122001"
    assert "9 Elsewhere Marg" in invoice.ship_to_address


def test_haryana_shop_with_a_delhi_delivery_address_stays_igst(client, db):
    _, sku = _store(db, warehouse_state="Delhi")
    user, retailer, _ = ready_retailer(db, warehouse_state="Haryana")
    delhi = _address_in(db, retailer, "Delhi")

    order_id = _order_via_api(client, user, sku, delhi)
    invoice = _dispatch(db, order_id)

    assert _tax_rows(db, order_id) == [("IGST", Decimal("12.00"))]
    assert invoice.place_of_supply == "Haryana"
    assert invoice.is_inter_state is True
    assert invoice.cgst_amount == Decimal("0.00") and invoice.igst_amount > 0
    assert invoice.ship_to_state == "Delhi"
    assert invoice.ship_to_pincode == "110002"


def test_same_state_delivery_address_taxes_and_places_supply_as_before(client, db):
    _, sku = _store(db, warehouse_state="Delhi")
    user, retailer, address = ready_retailer(db, warehouse_state="Delhi")

    order_id = _order_via_api(client, user, sku, address)
    invoice = _dispatch(db, order_id)

    assert _tax_rows(db, order_id) == [("CGST", Decimal("6.00")), ("SGST", Decimal("6.00"))]
    assert invoice.place_of_supply == "Delhi" and invoice.is_inter_state is False


def test_state_selection_has_one_site_the_registered_shop_state_helper(db, monkeypatch):
    retailer = Retailer(name="Helper Shop", state="Delhi")

    class _Warehouse:
        state = "Delhi"

    assert order_service._registered_shop_state(retailer) == "Delhi"
    assert order_service._is_inter_state(_Warehouse(), retailer) is False
    # If _is_inter_state did not consume the helper, patching it could not change the answer.
    monkeypatch.setattr(order_service, "_registered_shop_state", lambda _retailer: "Haryana")
    assert order_service._is_inter_state(_Warehouse(), retailer) is True


def test_invoice_place_of_supply_comes_from_the_same_helper(client, db, monkeypatch):
    _, sku = _store(db, warehouse_state="Delhi")
    user, retailer, address = ready_retailer(db, warehouse_state="Delhi")
    order_id = _order_via_api(client, user, sku, address)
    monkeypatch.setattr(order_service, "_registered_shop_state", lambda _retailer: "Haryana")

    invoice = _dispatch(db, order_id)

    assert invoice.place_of_supply == "Haryana"
    assert invoice.is_inter_state is True


def test_address_and_shop_edits_never_rewrite_an_order_or_its_issued_invoice(client, db):
    _, sku = _store(db, warehouse_state="Delhi")
    user, retailer, _ = ready_retailer(db, warehouse_state="Delhi")
    haryana = _address_in(db, retailer, "Haryana")
    order_id = _order_via_api(client, user, sku, haryana)

    # Before dispatch: edit then delete the saved address; the order snapshot stays put.
    patched = client.patch(
        f"/store/me/addresses/{haryana.id}", json={"line1": "Renamed Street"}, headers=auth_headers(user)
    )
    assert patched.status_code == 200, patched.text
    order = db.query(Order).filter(Order.id == order_id).one()
    db.refresh(order)
    assert order.ship_to_line1 == "9 Elsewhere Marg" and order.ship_to_state == "Haryana"
    deleted = client.delete(f"/store/me/addresses/{haryana.id}", headers=auth_headers(user))
    assert deleted.status_code == 204, deleted.text
    db.refresh(order)
    assert order.ship_to_line1 == "9 Elsewhere Marg" and order.ship_to_state == "Haryana"

    invoice = _dispatch(db, order_id)
    before = (
        invoice.place_of_supply,
        invoice.is_inter_state,
        invoice.ship_to_address,
        invoice.ship_to_state,
        invoice.cgst_amount,
        invoice.igst_amount,
        invoice.pdf_sha256,
    )
    tax_before = _tax_rows(db, order_id)

    # After issuance: the shop moves state and the invoice does not follow.
    retailer.state = "Karnataka"
    retailer.address_line1 = "Moved Lane"
    db.commit()
    db.refresh(invoice)

    after = (
        invoice.place_of_supply,
        invoice.is_inter_state,
        invoice.ship_to_address,
        invoice.ship_to_state,
        invoice.cgst_amount,
        invoice.igst_amount,
        invoice.pdf_sha256,
    )
    assert after == before
    assert _tax_rows(db, order_id) == tax_before
    pdf, _ = regenerate_invoice_pdf(db, invoice)
    assert hashlib.sha256(pdf.getvalue()).hexdigest() == invoice.pdf_sha256


def _salesman_invoice(db, *, retailer_state, warehouse_state="Delhi"):
    n = next(_n)
    warehouse = make_storefront(db, state=warehouse_state)
    sku = stocked_sku(
        db,
        warehouse=warehouse,
        brand=make_brand(db, f"Salesman Brand {n}"),
        category=make_category(db, f"Cat {n}"),
        name=f"Salesman SKU {n}",
        code=f"SAL-{n}",
        mrp="100",
        amount="100",
        gst_rate=Decimal("12"),
        qty=20,
    )
    retailer = Retailer(name=f"Salesman Shop {n}", state=retailer_state, address_line1="1 Shop Lane", pincode=110001)
    admin = User(email=f"sal-admin-{n}@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add_all([retailer, admin])
    db.commit()
    order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer.id,
            warehouse_id=warehouse.id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=2, unit_price=100, discount_amount=0)],
        ),
        admin,
    )
    return order, _dispatch(db, order.id)


def test_salesman_orders_are_taxed_and_invoiced_by_registered_state_with_no_ship_to(db):
    intra_order, intra = _salesman_invoice(db, retailer_state="Delhi")
    inter_order, inter = _salesman_invoice(db, retailer_state="Haryana")

    assert (intra.place_of_supply, intra.is_inter_state) == ("Delhi", False)
    assert (inter.place_of_supply, inter.is_inter_state) == ("Haryana", True)
    assert _tax_rows(db, intra_order.id) == [("CGST", Decimal("6.00")), ("SGST", Decimal("6.00"))]
    assert _tax_rows(db, inter_order.id) == [("IGST", Decimal("12.00"))]
    for invoice in (intra, inter):
        assert invoice.ship_to_address is None
        assert invoice.ship_to_state is None
        assert invoice.ship_to_pincode is None
