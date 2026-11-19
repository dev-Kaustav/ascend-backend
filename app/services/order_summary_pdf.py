"""The order summary PDF staff can download before dispatch issues the invoice.

It is not a tax invoice and says so in its title: no invoice number, no CGST/SGST split, and
it is rendered from the live order (via order.get_order_summary) rather than a frozen snapshot,
so it can change until the order ships. Every figure is read off that serialized order, which
takes them from finance.py; nothing here computes an amount.
"""
from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer
from sqlalchemy.orm import Session

from app.models import CompanyProfile
from app.services.invoice_pdf import (
    FONT,
    FONT_BOLD,
    MARGIN,
    PAGE_WIDTH,
    STYLE_FOOTER,
    STYLE_HEADER,
    STYLE_LABEL,
    STYLE_NORMAL,
    STYLE_NORMAL_BOLD,
    STYLE_SMALL,
    STYLE_SMALL_RIGHT,
    STYLE_TITLE,
    _company_profile,
    _esc,
    _money,
    _p,
)

CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
BORDER = colors.Color(0.7, 0.7, 0.7)
STYLE_SUBTITLE = ParagraphStyle("subtitle", fontName=FONT, fontSize=8, leading=10, alignment=2)

BOX_STYLE = [
    ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 6),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
]


def _date(value) -> str:
    try:
        return value.strftime("%B %d, %Y")
    except AttributeError:
        return str(value or "")[:10]


def _build_header(company: CompanyProfile):
    left = [_p(_esc(company.legal_name or "Ascend Foods"), STYLE_HEADER)]
    for line in [
        ", ".join(filter(None, [company.address_line1, company.address_line2, company.city])),
        ", ".join(filter(None, [company.state, company.pincode])),
        f"GSTIN: {company.gstin}" if company.gstin else None,
        f"Contact: {company.phone}" if company.phone else None,
        f"E-Mail: {company.email}" if company.email else None,
    ]:
        if line:
            left.append(_p(_esc(line), STYLE_SMALL))
    right = [_p("ORDER SUMMARY", STYLE_TITLE), _p("Not a tax invoice", STYLE_SUBTITLE)]
    tbl = Table([[left, right]], colWidths=[CONTENT_WIDTH * 0.65, CONTENT_WIDTH * 0.35])
    tbl.setStyle(TableStyle(BOX_STYLE))
    return tbl


def _build_info(order: dict):
    status = str(order.get("status") or "").replace("_", " ").title()
    tbl = Table(
        [[
            _p(f"ORDER: <b>#{order['id']}</b>", STYLE_NORMAL),
            _p(f"Order Date: {_date(order.get('created_at'))}", STYLE_NORMAL),
            _p(f"Status: {_esc(status)}", STYLE_NORMAL),
        ]],
        colWidths=[CONTENT_WIDTH / 3] * 3,
    )
    tbl.setStyle(TableStyle(BOX_STYLE + [("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    return tbl


def _build_addresses(order: dict):
    name = _esc(order.get("retailer_name") or "-")

    def block(heading, parts):
        lines = [_p(f"<b>{heading}</b>", STYLE_LABEL), _p(f"<b>{name}</b>", STYLE_NORMAL_BOLD)]
        for part in filter(None, parts):
            lines.append(_p(_esc(part), STYLE_SMALL))
        return lines

    pincode = str(order["retailer_pincode"]) if order.get("retailer_pincode") else None
    bill_parts = [
        ", ".join(filter(None, [order.get("retailer_address_line1"), order.get("retailer_city")])),
        ", ".join(filter(None, [order.get("retailer_state"), pincode])),
        f"GSTIN: {order['retailer_gst_number']}" if order.get("retailer_gst_number") else None,
    ]
    if order.get("ship_to_line1"):
        ship_pincode = str(order["ship_to_pincode"]) if order.get("ship_to_pincode") else None
        ship_parts = [
            ", ".join(filter(None, [order.get("ship_to_line1"), order.get("ship_to_line2"), order.get("ship_to_landmark")])),
            ", ".join(filter(None, [order.get("ship_to_city"), order.get("ship_to_state"), ship_pincode])),
        ]
    else:
        ship_parts = bill_parts[:2]

    tbl = Table(
        [[block("Bill To:", bill_parts), block("Ship To:", ship_parts)]],
        colWidths=[CONTENT_WIDTH * 0.5, CONTENT_WIDTH * 0.5],
    )
    tbl.setStyle(TableStyle(BOX_STYLE + [("LINEBEFORE", (1, 0), (1, -1), 0.5, BORDER)]))
    return tbl


def _gst_rate(item: dict) -> str:
    rate = sum((Decimal(str(tax["rate"])) for tax in item.get("taxes") or []), Decimal(0))
    return f"{rate:.2f}" if rate else "-"


def _build_items(order: dict):
    header = ["Sr.", "Item Description", "HSN", "Qty", "Rate", "Disc", "Taxable", "GST %", "GST", "Amount"]
    fixed = [18, 44, 30, 44, 44, 52, 32, 44, 52]
    col_widths = [fixed[0], CONTENT_WIDTH - sum(fixed)] + fixed[1:]

    data = [header]
    for number, item in enumerate(order.get("items") or [], start=1):
        description = item.get("sku_name") or item.get("sku_code") or f"SKU {item.get('sku_id')}"
        data.append([
            str(number),
            _p(_esc(description), STYLE_SMALL),
            item.get("hsn_code") or "-",
            format(Decimal(str(item.get("quantity") or 0)).normalize(), "f"),
            _money(item.get("unit_price")),
            _p(_money(item.get("discount_amount")), STYLE_SMALL_RIGHT),
            _money(item.get("taxable_value")),
            _gst_rate(item),
            _money(item.get("gst_amount")),
            _money(item.get("line_total")),
        ])

    delivery = order.get("delivery") or {}
    if delivery.get("line_total"):
        data.append([
            str(len(data)),
            _p("Delivery charge", STYLE_SMALL),
            "-", "-", "-", "-",
            _money(delivery["taxable_value"]),
            "-",
            _money(delivery["gst_amount"]),
            _money(delivery["line_total"]),
        ])

    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.93, 0.93, 0.93)),
        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("LEADING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (1, 0), (1, -1), "LEFT"),
    ]))
    return tbl


def _build_totals(order: dict):
    rows = [
        ["Sub Total", _money(order.get("taxable_value"))],
        ["GST", _money(order.get("gst_amount"))],
        ["Total", f"Rs. {_money(order.get('grand_total'))}"],
    ]
    paid = Decimal(str(order.get("amount_paid") or 0))
    if paid:
        rows.append(["Paid", _money(paid)])
        rows.append(["Pending", _money(order.get("pending_amount"))])

    totals = Table(rows, colWidths=[CONTENT_WIDTH * 0.35, CONTENT_WIDTH * 0.15])
    totals.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTNAME", (0, 2), (-1, 2), FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LEADING", (0, 0), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
    ]))
    # Right-aligned under the items table, as on the invoice; zero padding so the nested
    # table's right edge lines up with the items table's.
    wrapper = Table([["", totals]], colWidths=[CONTENT_WIDTH * 0.5, CONTENT_WIDTH * 0.5])
    wrapper.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return wrapper


def render_order_summary_pdf(order: dict, company: CompanyProfile) -> BytesIO:
    """Render the summary from the dict order.get_order_summary returns."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
        title=f"Order #{order['id']} summary",
    )
    doc.build([
        _build_header(company),
        Spacer(1, 2 * mm),
        _build_info(order),
        Spacer(1, 2 * mm),
        _build_addresses(order),
        Spacer(1, 3 * mm),
        _build_items(order),
        Spacer(1, 2 * mm),
        _build_totals(order),
        Spacer(1, 4 * mm),
        _p("This is an order summary, not a tax invoice. The GST invoice is issued when the order "
           "is dispatched, and amounts may change until then.", STYLE_FOOTER),
    ])
    buf.seek(0)
    return buf


def order_summary_pdf(db: Session, order: dict) -> tuple[BytesIO, str]:
    return render_order_summary_pdf(order, _company_profile(db)), f"order-{order['id']}-summary.pdf"
