from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from sqlalchemy.orm import Session

from app.models import CompanyProfile
from app.services.order import get_order_invoice_view

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 15 * mm

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

STYLE_HEADER = ParagraphStyle("header", fontName=FONT_BOLD, fontSize=14, leading=17)
STYLE_TITLE = ParagraphStyle("title", fontName=FONT_BOLD, fontSize=16, leading=20, alignment=2)
STYLE_NORMAL = ParagraphStyle("normal", fontName=FONT, fontSize=8, leading=10)
STYLE_NORMAL_BOLD = ParagraphStyle("normalBold", fontName=FONT_BOLD, fontSize=8, leading=10)
STYLE_SMALL = ParagraphStyle("small", fontName=FONT, fontSize=7, leading=9)
STYLE_LABEL = ParagraphStyle("label", fontName=FONT_BOLD, fontSize=8, leading=10)
STYLE_TOTAL_LABEL = ParagraphStyle("totalLabel", fontName=FONT_BOLD, fontSize=9, leading=12)
STYLE_TOTAL_VALUE = ParagraphStyle("totalValue", fontName=FONT_BOLD, fontSize=9, leading=12, alignment=2)
STYLE_FOOTER = ParagraphStyle("footer", fontName=FONT, fontSize=7, leading=9)
STYLE_FOOTER_BOLD = ParagraphStyle("footerBold", fontName=FONT_BOLD, fontSize=7, leading=9)

GRID_STYLE = TableStyle([
    ("GRID", (0, 0), (-1, -1), 0.5, colors.Color(0.7, 0.7, 0.7)),
    ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.95, 0.95, 0.95)),
    ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
    ("FONTSIZE", (0, 0), (-1, -1), 7),
    ("LEADING", (0, 0), (-1, -1), 9),
    ("TOPPADDING", (0, 0), (-1, -1), 3),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ("LEFTPADDING", (0, 0), (-1, -1), 3),
    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
])


def _p(text, style=STYLE_NORMAL):
    return Paragraph(str(text) if text else "", style)


def _money(value) -> str:
    v = float(value or 0)
    return f"{v:,.2f}"


def _company_profile(db: Session) -> CompanyProfile:
    profile = db.query(CompanyProfile).order_by(CompanyProfile.id.asc()).first()
    if profile:
        return profile
    return CompanyProfile(legal_name="Ascend Foods", invoice_prefix="ASC", invoice_next_number=1)


def _build_header_table(company: CompanyProfile):
    left_lines = [_p(company.legal_name or "Ascend Foods", STYLE_HEADER)]
    for line in [
        company.address_line1,
        company.address_line2,
        ", ".join(filter(None, [company.city, company.state, str(company.pincode or "")])),
        f"GSTIN: {company.gstin}" if company.gstin else None,
        f"Contact: {company.phone}" if company.phone else None,
        f"E-Mail: {company.email}" if company.email else None,
    ]:
        if line:
            left_lines.append(_p(line, STYLE_SMALL))

    right = _p("TAX INVOICE", STYLE_TITLE)
    content_width = PAGE_WIDTH - 2 * MARGIN

    tbl = Table(
        [[left_lines, right]],
        colWidths=[content_width * 0.65, content_width * 0.35],
    )
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.Color(0.7, 0.7, 0.7)),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


def _build_order_info_table(order, invoice_date):
    content_width = PAGE_WIDTH - 2 * MARGIN
    data = [
        [
            _p(f"Order Date: {invoice_date}", STYLE_NORMAL),
            _p(f"Order ID: {order.get('id', '')}", STYLE_NORMAL),
        ],
        [
            _p(f"Salesman Name: <b>{order.get('salesman_name') or '-'}</b>", STYLE_NORMAL),
            _p(f"Payment Mode: <b>{order.get('payment_status') or 'CREDIT'}</b>", STYLE_NORMAL),
        ],
        [
            _p(f"INVOICE NUMBER: <b>{order.get('invoice_number') or '-'}</b>", STYLE_NORMAL),
            _p(""),
        ],
    ]
    tbl = Table(data, colWidths=[content_width * 0.5, content_width * 0.5])
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.Color(0.7, 0.7, 0.7)),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


def _build_address_table(order):
    content_width = PAGE_WIDTH - 2 * MARGIN
    retailer_name = order.get("retailer_name") or f"Retailer {order.get('to_entity_id', '')}"
    addr_parts = filter(None, [
        order.get("retailer_address_line1"),
        ", ".join(filter(None, [order.get("retailer_city"), order.get("retailer_state")])),
        f"Pincode: {order.get('retailer_pincode')}" if order.get("retailer_pincode") else None,
    ])

    def _addr_block(heading):
        lines = [_p(f"<b>{heading}</b>", STYLE_LABEL), _p(f"<b>{retailer_name}</b>", STYLE_NORMAL_BOLD)]
        for part in filter(None, [
            order.get("retailer_address_line1"),
            ", ".join(filter(None, [order.get("retailer_city"), order.get("retailer_state")])),
            f"Pincode: {order.get('retailer_pincode')}" if order.get("retailer_pincode") else None,
            f"GSTIN: {order.get('retailer_gst_number')}" if order.get("retailer_gst_number") else None,
        ]):
            lines.append(_p(part, STYLE_SMALL))
        return lines

    data = [[_addr_block("Bill To:"), _addr_block("Ship To:")]]
    tbl = Table(data, colWidths=[content_width * 0.5, content_width * 0.5])
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.Color(0.7, 0.7, 0.7)),
        ("LINEBEFORE", (1, 0), (1, -1), 0.5, colors.Color(0.7, 0.7, 0.7)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


def _extract_taxes(item):
    taxes = item.get("taxes", [])
    sgst_pct = sgst_amt = cgst_pct = cgst_amt = igst_pct = igst_amt = 0.0
    for tax in taxes:
        tt = (tax.get("tax_type") or "").upper()
        rate = float(tax.get("rate") or 0)
        taxable = float(item.get("taxable_value") or 0)
        amt = round(taxable * rate / 100, 2)
        if tt == "SGST":
            sgst_pct = rate
            sgst_amt = amt
        elif tt == "CGST":
            cgst_pct = rate
            cgst_amt = amt
        elif tt == "IGST":
            igst_pct = rate
            igst_amt = amt
    return sgst_pct, sgst_amt, cgst_pct, cgst_amt, igst_pct, igst_amt


def _build_items_table(items):
    content_width = PAGE_WIDTH - 2 * MARGIN
    col_widths = [
        20, content_width * 0.18, 48, 28, 42, 38, 48, 32, 38, 32, 38, 48
    ]

    header = ["Sr.", "Item Description", "HSN", "Qty", "MRP", "Disc", "Rate",
              "SGST %", "SGST", "CGST %", "CGST", "Amount"]
    data = [header]

    total_discount = 0.0
    total_sgst = 0.0
    total_cgst = 0.0

    for i, item in enumerate(items, 1):
        sgst_pct, sgst_amt, cgst_pct, cgst_amt, igst_pct, igst_amt = _extract_taxes(item)
        qty = float(item.get("quantity") or 0)
        unit_price = float(item.get("unit_price") or 0)
        discount = float(item.get("discount_amount") or 0)
        taxable = float(item.get("taxable_value") or 0)
        line_total = float(item.get("line_total") or 0)

        total_discount += discount * qty if discount else 0
        total_sgst += sgst_amt
        total_cgst += cgst_amt

        disc_str = f"{_money(discount)}({0}%)" if discount else "0(0%)"
        rate = _money(taxable)

        data.append([
            str(i),
            _p(item.get("sku_name") or f"SKU {item.get('sku_id')}", STYLE_SMALL),
            item.get("hsn_code") or "-",
            str(int(qty)) if qty == int(qty) else _money(qty),
            _money(unit_price),
            disc_str,
            rate,
            f"{sgst_pct:.2f}" if sgst_pct else "-",
            _money(sgst_amt) if sgst_amt else "-",
            f"{cgst_pct:.2f}" if cgst_pct else "-",
            _money(cgst_amt) if cgst_amt else "-",
            _money(line_total),
        ])

    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    style = TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.Color(0.7, 0.7, 0.7)),
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
    ])
    tbl.setStyle(style)
    return tbl, total_discount, total_sgst, total_cgst


def _build_totals_table(invoice, order, total_discount, total_sgst, total_cgst):
    content_width = PAGE_WIDTH - 2 * MARGIN
    sub_total = float(invoice.get("taxable_value") or 0)
    grand_total = float(invoice.get("grand_total") or 0)

    rows = [
        ["Sub Total", _money(sub_total)],
        ["Items Discount", _money(total_discount)],
        ["SGST", _money(total_sgst)],
        ["CGST", _money(total_cgst)],
        ["Total", f"Rs. {_money(grand_total)}"],
    ]

    tbl = Table(rows, colWidths=[content_width * 0.35, content_width * 0.15])
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.Color(0.7, 0.7, 0.7)),
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTNAME", (0, -1), (-1, -1), FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LEADING", (0, 0), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
    ]))
    return tbl


def _build_footer_table(order, company):
    content_width = PAGE_WIDTH - 2 * MARGIN
    inv = order.get("invoice_number") or ""
    company_name = company.legal_name or "Ascend Foods"
    left = _p(f"<b>{inv}</b> ({company_name})", STYLE_FOOTER_BOLD)
    right_lines = [
        _p(company_name, STYLE_FOOTER),
        Spacer(1, 20),
        _p("Authorized Signatory", STYLE_FOOTER),
    ]
    tbl = Table([[left, right_lines]], colWidths=[content_width * 0.6, content_width * 0.4])
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.Color(0.7, 0.7, 0.7)),
        ("VALIGN", (0, 0), (0, -1), "MIDDLE"),
        ("VALIGN", (1, 0), (1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


def generate_invoice_pdf(db: Session, order_id: int) -> tuple[BytesIO, str]:
    invoice = get_order_invoice_view(db, order_id)
    order = invoice["order"]
    company = _company_profile(db)
    filename = f"{order.get('invoice_number') or f'order-{order_id}'}.pdf"

    created_at = order.get("created_at")
    if created_at:
        try:
            invoice_date = created_at.strftime("%B %d, %Y")
        except AttributeError:
            invoice_date = str(created_at)[:10]
    else:
        invoice_date = ""

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
    )

    elements = []

    elements.append(_build_header_table(company))
    elements.append(Spacer(1, 2 * mm))
    elements.append(_build_order_info_table(order, invoice_date))
    elements.append(Spacer(1, 2 * mm))
    elements.append(_build_address_table(order))
    elements.append(Spacer(1, 3 * mm))

    items = order.get("items", [])
    items_tbl, total_discount, total_sgst, total_cgst = _build_items_table(items)
    elements.append(items_tbl)
    elements.append(Spacer(1, 2 * mm))

    totals_tbl = _build_totals_table(invoice, order, total_discount, total_sgst, total_cgst)
    content_width = PAGE_WIDTH - 2 * MARGIN
    wrapper = Table(
        [["", totals_tbl]],
        colWidths=[content_width * 0.5, content_width * 0.5],
    )
    wrapper.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(wrapper)

    elements.append(Spacer(1, 4 * mm))
    elements.append(_build_footer_table(order, company))

    footer_text = company.invoice_footer or "This is a computer generated invoice."
    elements.append(Spacer(1, 2 * mm))
    elements.append(_p(footer_text, STYLE_FOOTER))

    doc.build(elements)
    buf.seek(0)
    return buf, filename
