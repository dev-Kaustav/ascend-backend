"""The pre-invoice order summary PDF: available before dispatch, shows the order's own totals,
and never calls itself a tax invoice."""
from app.core.security import create_access_token
from app.models import Brand, CompanyProfile, Inventory, Retailer, SKU, SKUBatch, User, Warehouse
from app.models.enums import EmployeeRole
from app.schemas.order import OrderCreate, OrderItemCreate
from app.services.order import create_outgoing_order, get_order_summary
from app.services.order_summary_pdf import render_order_summary_pdf
from app.tests.test_invoice_pdf import _decoded_page_text


def _pending_order(db):
    brand = Brand(name="Summary Brand")
    warehouse = Warehouse(name="Summary WH", location="Loc", state="Delhi")
    retailer = Retailer(name="Summary & Sons <Retail>", state="Delhi", address_line1="1 Lane", city="City", pincode=110001)
    db.add_all([brand, warehouse, retailer])
    db.commit()
    sku = SKU(name="Summary SKU", brand_id=brand.id, hsn_code="20081920", sgst_percent=9, cgst_percent=9)
    db.add(sku)
    db.commit()
    db.add_all([
        SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=10, remaining_quantity=10),
        Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=10),
    ])
    admin = User(email="summary-admin@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add(admin)
    db.commit()
    order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer.id,
            warehouse_id=warehouse.id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=3, unit_price=100, discount_amount=30)],
        ),
        admin,
    )
    return order, admin


def test_summary_pdf_is_served_before_any_invoice_exists(client, db):
    order, admin = _pending_order(db)
    assert order.invoice is None
    token = create_access_token({"user_id": admin.id, "role": EmployeeRole.ADMIN.value})

    response = client.get(f"/orders/{order.id}/summary.pdf", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert f"order-{order.id}-summary.pdf" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")


def test_summary_pdf_404s_for_unknown_order(client, db):
    _, admin = _pending_order(db)
    token = create_access_token({"user_id": admin.id, "role": EmployeeRole.ADMIN.value})
    response = client.get("/orders/999999/summary.pdf", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404


def test_summary_prints_the_order_totals_and_is_labelled_not_an_invoice(db):
    order, admin = _pending_order(db)
    summary = get_order_summary(db, order.id, admin)

    text = _decoded_page_text(render_order_summary_pdf(summary, CompanyProfile(legal_name="Ascend Foods")).getvalue())

    assert b"ORDER SUMMARY" in text
    assert b"Not a tax invoice" in text
    assert b"TAX INVOICE" not in text
    assert f"{summary['grand_total']:,.2f}".encode() in text
    assert f"{summary['gst_amount']:,.2f}".encode() in text
