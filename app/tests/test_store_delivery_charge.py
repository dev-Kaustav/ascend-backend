"""Delivery charge on store orders (08-11, STORE-11, D-04..D-07, D-33, D-35, D-36).

Below the admin-set minimum cart value the server adds a GST-inclusive charge. It is part of the
order total, prints as its own invoice line, and the retailer only ever sees it as rupees.
"""
import itertools
from decimal import Decimal

import pytest

from app.models import Order, OrderItem, OrderItemTax
from app.models.enums import EmployeeRole
from app.services import finance
from app.services.finance import (
    calculate_order_outstanding,
    calculate_order_totals,
    compute_delivery_charge,
    delivery_charge_totals,
    inclusive_tax_amount,
)
from app.services.invoice import DELIVERY_CHARGE_UQC
from app.services.invoice_export import build_invoice_export_rows
from app.services.invoice_pdf import regenerate_invoice_pdf
from app.services.store_settings import get_store_settings
from app.tests.store_helpers import (
    auth_headers,
    make_brand,
    make_category,
    make_storefront,
    ready_retailer,
    stocked_sku,
)
from app.tests.test_invoice_pdf import _decoded_page_text
from app.tests.test_store_invoice import _address_in, _dispatch

_n = itertools.count(1)
D = Decimal


# --- compute_delivery_charge / inclusive_tax_amount --------------------------------------------


@pytest.mark.parametrize(
    "cart, minimum, percent, expected",
    [
        (D("999.99"), 1000, 8, D("80.00")),
        (D("1000.00"), 1000, 8, D("0.00")),
        (D("1000.01"), 1000, 8, D("0.00")),
        (500, 0, 8, D("0.00")),
        (500, 1000, 0, D("0.00")),
        (D("0.05"), 1000, 10, D("0.01")),
        (D("450.00"), D("1000.00"), D("8.00"), D("36.00")),
        (D("0.00"), 1000, 8, D("0.00")),
    ],
)
def test_compute_delivery_charge_boundaries_and_rounding(cart, minimum, percent, expected):
    charge = compute_delivery_charge(cart, minimum, percent)
    assert charge == expected
    assert isinstance(charge, Decimal)


def test_inclusive_tax_amount_is_the_formula_order_items_use():
    item = type(
        "Item",
        (),
        {
            "quantity": 1,
            "unit_price": D("36.00"),
            "discount_amount": D("0"),
            "taxes": [type("T", (), {"rate": D("12")})()],
        },
    )()
    assert inclusive_tax_amount(D("36.00"), D("12")) == finance._order_item_tax_amount(item) == D("4.32")


def test_delivery_charge_totals_split_the_inclusive_charge_exactly():
    order = type("O", (), {"delivery_charge": D("36.00"), "delivery_charge_gst_rate": D("12.00")})()
    totals = delivery_charge_totals(order)
    assert totals["line_total"] == D("36.00")
    assert totals["gst_amount"] == inclusive_tax_amount(D("36.00"), D("12.00"))
    assert totals["taxable_value"] + totals["gst_amount"] == D("36.00")


def test_delivery_charge_totals_are_zero_without_a_charge():
    for order in (
        type("O", (), {"delivery_charge": D("0"), "delivery_charge_gst_rate": D("0")})(),
        type("O", (), {})(),
        type("O", (), {"delivery_charge": None, "delivery_charge_gst_rate": None})(),
    ):
        assert delivery_charge_totals(order) == {
            "taxable_value": D("0.00"),
            "gst_amount": D("0.00"),
            "line_total": D("0.00"),
        }


# --- store fixtures ------------------------------------------------------------------------------


def _settings(db, minimum, percent):
    row = get_store_settings(db)
    row.min_order_value = D(str(minimum))
    row.delivery_charge_percent = D(str(percent))
    db.commit()


def _catalogue(db, *, warehouse_state="Delhi", minimum="1000", percent="8"):
    warehouse = make_storefront(db, state=warehouse_state)
    _settings(db, minimum, percent)
    return warehouse, make_brand(db), make_category(db, "Chips")


def _sku(db, catalogue, *, mrp, amount, gst=12, hsn="2008", qty=500):
    warehouse, brand, category = catalogue
    n = next(_n)
    sku = stocked_sku(
        db,
        warehouse=warehouse,
        brand=brand,
        category=category,
        name=f"Chip {n}",
        code=f"DEL-{n}",
        mrp=mrp,
        amount=amount,
        gst_rate=D(str(gst)),
        qty=qty,
    )
    sku.hsn_code = hsn
    db.commit()
    return sku


def _place(client, user, address, *lines):
    response = client.post(
        "/store/orders",
        json={
            "items": [{"sku_id": sku.id, "quantity": quantity} for sku, quantity in lines],
            "address_id": address.id,
        },
        headers=auth_headers(user),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _order(db, order_id):
    order = db.query(Order).filter(Order.id == order_id).one()
    db.refresh(order)
    return order


# --- the tracer: charge in totals and on the invoice ----------------------------------------------


def test_a_below_minimum_order_carries_the_charge_into_its_totals(client, db):
    catalogue = _catalogue(db)
    sku = _sku(db, catalogue, mrp="22.5", amount="18.0")
    user, _, address = ready_retailer(db)

    order = _order(db, _place(client, user, address, (sku, 25)))  # 25 x 18.00 = 450.00

    assert order.delivery_charge == D("36.00")
    assert order.delivery_charge_gst_rate == D("12.00")
    assert calculate_order_totals(order)["grand_total"] == D("486.00")
    assert calculate_order_outstanding(order) == D("486.00")


def test_the_charge_prints_as_its_own_gst_line_after_the_items(client, db):
    catalogue = _catalogue(db)
    sku = _sku(db, catalogue, mrp="22.5", amount="18.0")
    user, _, address = ready_retailer(db)
    invoice = _dispatch(db, _place(client, user, address, (sku, 25)))

    assert [line.line_number for line in invoice.lines] == [1, 2]
    item_line, delivery = invoice.lines
    assert item_line.sku_id == sku.id
    assert delivery.sku_id is None
    assert delivery.description == "Delivery charge"
    assert delivery.quantity == 1
    assert delivery.unit_rate == D("36.00")
    assert delivery.discount_amount == D("0.00")
    assert delivery.taxable_value + delivery.total_tax_amount == D("36.00")
    assert delivery.line_total == D("36.00")
    assert delivery.cgst_amount + delivery.sgst_amount == delivery.total_tax_amount
    assert delivery.igst_amount == D("0.00")
    assert (delivery.cgst_rate, delivery.sgst_rate) == (D("6.00"), D("6.00"))
    assert delivery.hsn_code == "2008"
    assert delivery.uqc == DELIVERY_CHARGE_UQC == "OTH"

    assert invoice.grand_total == sum((l.line_total for l in invoice.lines), D("0")) == D("486.00")
    assert invoice.taxable_value == sum((l.taxable_value for l in invoice.lines), D("0"))
    assert invoice.total_tax_amount == sum((l.total_tax_amount for l in invoice.lines), D("0"))
    assert invoice.cgst_amount == sum((l.cgst_amount for l in invoice.lines), D("0"))

    pdf, _ = regenerate_invoice_pdf(db, invoice)
    pdf = pdf.getvalue()
    assert b"Delivery charge" in _decoded_page_text(pdf)


def test_a_cart_at_or_above_the_minimum_has_no_charge_and_no_extra_line(client, db):
    catalogue = _catalogue(db)
    sku = _sku(db, catalogue, mrp="25", amount="20")
    user, _, address = ready_retailer(db)

    order_id = _place(client, user, address, (sku, 50))  # exactly 1000.00
    order = _order(db, order_id)
    assert order.delivery_charge == D("0.00") and order.delivery_charge_gst_rate == D("0.00")
    assert calculate_order_totals(order)["grand_total"] == D("1000.00")

    invoice = _dispatch(db, order_id)
    assert len(invoice.lines) == 1
    assert invoice.lines[0].sku_id == sku.id
    assert invoice.grand_total == D("1000.00")


def test_a_cart_just_below_the_minimum_is_charged_on_the_whole_cart(client, db):
    catalogue = _catalogue(db)
    sku = _sku(db, catalogue, mrp="333.33", amount="333.33")
    user, _, address = ready_retailer(db)

    order = _order(db, _place(client, user, address, (sku, 3)))  # 999.99

    assert order.delivery_charge == D("80.00")


def test_a_zero_minimum_never_charges(client, db):
    catalogue = _catalogue(db, minimum="0")
    sku = _sku(db, catalogue, mrp="22.5", amount="18.0")
    user, _, address = ready_retailer(db)

    order = _order(db, _place(client, user, address, (sku, 1)))

    assert order.delivery_charge == D("0.00")


def test_the_charge_is_never_client_supplied(client, db):
    catalogue = _catalogue(db)
    sku = _sku(db, catalogue, mrp="22.5", amount="18.0")
    user, _, address = ready_retailer(db)

    response = client.post(
        "/store/orders",
        json={
            "items": [{"sku_id": sku.id, "quantity": 25}],
            "address_id": address.id,
            "delivery_charge": "0",
            "delivery_charge_percent": 0,
            "delivery_percent": 0,
        },
        headers=auth_headers(user),
    )
    assert response.status_code == 201, response.text
    assert _order(db, response.json()["id"]).delivery_charge == D("36.00")


# --- D-33 / D-36: rate and HSN of the delivery line -------------------------------------------------


def test_the_delivery_line_takes_the_highest_rate_and_that_items_hsn(client, db):
    catalogue = _catalogue(db)
    low = _sku(db, catalogue, mrp="100", amount="100", gst=5, hsn="1111")
    high = _sku(db, catalogue, mrp="100", amount="100", gst=12, hsn="2222")
    user, _, address = ready_retailer(db)

    order_id = _place(client, user, address, (low, 2), (high, 2))  # 400.00 -> 32.00
    order = _order(db, order_id)
    assert order.delivery_charge == D("32.00")
    assert order.delivery_charge_gst_rate == D("12.00")

    invoice = _dispatch(db, order_id)
    delivery = invoice.lines[-1]
    assert [l.hsn_code for l in invoice.lines[:2]] == ["1111", "2222"]
    assert delivery.hsn_code == "2222"
    assert delivery.uqc == "OTH"
    assert (delivery.cgst_rate, delivery.sgst_rate) == (D("6.00"), D("6.00"))
    assert delivery.taxable_value + delivery.total_tax_amount == D("32.00")

    rows = [r for r in build_invoice_export_rows(db) if r["Invoice Number"] == invoice.invoice_number]
    export_delivery = rows[-1]
    assert export_delivery["Description"] == "Delivery charge"
    assert export_delivery["HSN"] == "2222" and export_delivery["UQC"] == "OTH"
    assert export_delivery["Line Number"] == 3
    assert len(rows) == 3

    pdf, _ = regenerate_invoice_pdf(db, invoice)
    assert b"2222" in _decoded_page_text(pdf.getvalue())


def test_equal_top_rates_pick_the_lowest_line_number_hsn(client, db):
    catalogue = _catalogue(db)
    first = _sku(db, catalogue, mrp="100", amount="100", gst=12, hsn="3333")
    second = _sku(db, catalogue, mrp="100", amount="100", gst=12, hsn="4444")
    user, _, address = ready_retailer(db)

    invoice = _dispatch(db, _place(client, user, address, (first, 1), (second, 1)))

    assert invoice.lines[-1].hsn_code == "3333"


def test_an_item_without_an_hsn_leaves_the_delivery_hsn_null(client, db):
    catalogue = _catalogue(db)
    sku = _sku(db, catalogue, mrp="100", amount="100", gst=12, hsn=None)
    user, _, address = ready_retailer(db)

    invoice = _dispatch(db, _place(client, user, address, (sku, 1)))

    assert invoice.lines[-1].hsn_code is None
    assert invoice.lines[-1].uqc == "OTH"


def test_a_delivery_rate_equal_to_an_items_rate_is_never_merged_into_the_item(client, db):
    catalogue = _catalogue(db)
    sku = _sku(db, catalogue, mrp="100", amount="100", gst=12)
    user, _, address = ready_retailer(db)

    invoice = _dispatch(db, _place(client, user, address, (sku, 2)))

    assert len(invoice.lines) == 2
    assert invoice.lines[0].line_total == D("200.00")
    assert invoice.lines[1].description == "Delivery charge"


def test_editing_the_source_sku_hsn_never_changes_the_issued_delivery_line(client, db):
    catalogue = _catalogue(db)
    sku = _sku(db, catalogue, mrp="22.5", amount="18.0", hsn="5555")
    user, _, address = ready_retailer(db)
    invoice = _dispatch(db, _place(client, user, address, (sku, 25)))
    pdf_before = invoice.pdf_sha256

    sku.hsn_code = "9999"
    db.commit()
    db.refresh(invoice)

    assert invoice.lines[-1].hsn_code == "5555"
    export = [r for r in build_invoice_export_rows(db) if r["Invoice Number"] == invoice.invoice_number]
    assert export[-1]["HSN"] == "5555"
    pdf, _ = regenerate_invoice_pdf(db, invoice)
    import hashlib

    assert hashlib.sha256(pdf.getvalue()).hexdigest() == pdf_before


# --- D-35: delivery tax type follows the registered shop state ---------------------------------------


def test_delhi_shop_with_a_haryana_ship_to_pays_cgst_sgst_on_goods_and_delivery(client, db):
    catalogue = _catalogue(db, warehouse_state="Delhi")
    sku = _sku(db, catalogue, mrp="22.5", amount="18.0")
    user, retailer, _ = ready_retailer(db, warehouse_state="Delhi")
    haryana = _address_in(db, retailer, "Haryana")

    invoice = _dispatch(db, _place(client, user, haryana, (sku, 25)))

    assert invoice.is_inter_state is False
    for line in invoice.lines:
        assert line.igst_amount == D("0.00")
        assert line.cgst_amount > 0 and line.sgst_amount > 0
    assert invoice.lines[-1].cgst_rate == D("6.00")


def test_haryana_shop_with_a_delhi_ship_to_pays_igst_on_goods_and_delivery(client, db):
    catalogue = _catalogue(db, warehouse_state="Delhi")
    low = _sku(db, catalogue, mrp="100", amount="100", gst=5)
    high = _sku(db, catalogue, mrp="100", amount="100", gst=12)
    user, retailer, _ = ready_retailer(db, warehouse_state="Haryana")
    delhi = _address_in(db, retailer, "Delhi")

    order_id = _place(client, user, delhi, (low, 2), (high, 2))
    assert _order(db, order_id).delivery_charge_gst_rate == D("12.00")
    invoice = _dispatch(db, order_id)

    assert invoice.is_inter_state is True
    for line in invoice.lines:
        assert line.cgst_amount == D("0.00") and line.sgst_amount == D("0.00")
        assert line.igst_amount > 0
    delivery = invoice.lines[-1]
    assert delivery.igst_rate == D("12.00")
    assert delivery.igst_amount == delivery.total_tax_amount
    assert invoice.igst_amount == sum((l.igst_amount for l in invoice.lines), D("0"))


# --- salesman / admin orders are untouched ------------------------------------------------------------


def test_orders_without_a_charge_total_exactly_as_before(client, db):
    catalogue = _catalogue(db)
    sku = _sku(db, catalogue, mrp="100", amount="100", gst=12)
    user, _, address = ready_retailer(db)
    order = _order(db, _place(client, user, address, (sku, 10)))  # 1000.00

    assert order.delivery_charge == D("0.00")
    totals = calculate_order_totals(order)
    assert totals["grand_total"] == D("1000.00")
    assert totals["gst_amount"] == D("120.00")
