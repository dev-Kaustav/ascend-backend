from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from sqlalchemy.orm import Session

from app.models import CompanyProfile, Invoice

# No import of the order service module here, ever. That import is the re-derivation
# path INV-03 exists to retire: render_invoice_pdf's only inputs are the Invoice/InvoiceLine
# snapshot (both of which may be transient, unflushed objects) plus the live
# CompanyProfile for a handful of presentation-only fields that are not part of the
# legal snapshot (phone, email, footer text). No name, address, GSTIN, amount, rate or
# date is ever read from a live Order, OrderItem, OrderItemTax, SKU or Retailer.

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
    # Format the Decimal directly rather than routing through float(). A float round
    # trip is exactly the pattern Phase 1 spent a whole phase removing; there is no
    # reason to reintroduce it at a new call site, even in a presentation function.
    d = value if isinstance(value, Decimal) else Decimal(str(value or 0))
    return f"{d:,.2f}"


def _company_profile(db: Session) -> CompanyProfile:
    """Presentation-only fields not carried on the Invoice snapshot: phone, email,
    invoice_footer. Never source a name, address, GSTIN, amount, rate or date from this
    — those all live on the invoice itself."""
    profile = db.query(CompanyProfile).order_by(CompanyProfile.id.asc()).first()
    if profile:
        return profile
    return CompanyProfile(legal_name="Ascend Foods", invoice_prefix="ASC")


def _build_header_table(invoice: Invoice, company: CompanyProfile):
    left_lines = [_p(invoice.supplier_legal_name or "Ascend Foods", STYLE_HEADER)]
    for line in [
        invoice.supplier_address,
        ", ".join(filter(None, [invoice.supplier_state, invoice.supplier_pincode])),
        f"GSTIN: {invoice.supplier_gstin}" if invoice.supplier_gstin else None,
        # phone/email are not part of the snapshot — presentation-only, from the live
        # CompanyProfile, not from the invoice.
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


def _build_invoice_info_table(invoice: Invoice):
    content_width = PAGE_WIDTH - 2 * MARGIN
    try:
        invoice_date = invoice.invoice_date.strftime("%B %d, %Y")
    except AttributeError:
        invoice_date = str(invoice.invoice_date or "")[:10]

    data = [
        [
            _p(f"Invoice Date: {invoice_date}", STYLE_NORMAL),
            _p(f"Order ID: {invoice.order_id}", STYLE_NORMAL),
        ],
        [
            _p(f"INVOICE NUMBER: <b>{invoice.invoice_number or '-'}</b>", STYLE_NORMAL),
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


def _build_address_table(invoice: Invoice):
    content_width = PAGE_WIDTH - 2 * MARGIN
    buyer_name = invoice.buyer_name or "-"

    def _addr_block(heading):
        lines = [_p(f"<b>{heading}</b>", STYLE_LABEL), _p(f"<b>{buyer_name}</b>", STYLE_NORMAL_BOLD)]
        for part in filter(None, [
            invoice.buyer_address,
            ", ".join(filter(None, [invoice.buyer_state, invoice.buyer_pincode])),
            f"GSTIN: {invoice.buyer_gstin}" if invoice.buyer_gstin else None,
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


def _build_items_table(invoice: Invoice):
    """Built from `invoice.lines` alone — every tax figure is a stored column, never
    recomputed. There used to be an `_extract_taxes` helper here that independently
    computed `taxable * rate / 100`, a second tax computation disagreeing with
    `finance.py`'s `inclusive_value * rate / 100` (a direct D-03 violation). It is gone;
    the components below are read straight off the InvoiceLine snapshot."""
    content_width = PAGE_WIDTH - 2 * MARGIN
    inter_state = invoice.is_inter_state

    if inter_state:
        header = ["Sr.", "Item Description", "HSN", "Qty", "Unit Rate", "Disc",
                  "Taxable", "IGST %", "IGST", "Amount"]
        col_widths = [20, content_width * 0.22, 48, 28, 42, 38, 48, 34, 42, 52]
    else:
        header = ["Sr.", "Item Description", "HSN", "Qty", "Unit Rate", "Disc",
                  "Taxable", "SGST %", "SGST", "CGST %", "CGST", "Amount"]
        col_widths = [20, content_width * 0.16, 42, 24, 38, 34, 42, 30, 36, 30, 36, 46]

    data = [header]

    for line in invoice.lines:
        qty = line.quantity or 0
        row = [
            str(line.line_number),
            _p(line.description or f"SKU {line.sku_id}", STYLE_SMALL),
            line.hsn_code or "-",
            str(qty),
            _money(line.unit_rate),
            _money(line.discount_amount) if line.discount_amount else "0.00",
            _money(line.taxable_value),
        ]
        if inter_state:
            row += [
                f"{line.igst_rate:.2f}" if line.igst_rate else "-",
                _money(line.igst_amount) if line.igst_amount else "-",
            ]
        else:
            row += [
                f"{line.sgst_rate:.2f}" if line.sgst_rate else "-",
                _money(line.sgst_amount) if line.sgst_amount else "-",
                f"{line.cgst_rate:.2f}" if line.cgst_rate else "-",
                _money(line.cgst_amount) if line.cgst_amount else "-",
            ]
        row.append(_money(line.line_total))
        data.append(row)

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
    return tbl


def _build_totals_table(invoice: Invoice):
    content_width = PAGE_WIDTH - 2 * MARGIN

    rows = [["Sub Total", _money(invoice.taxable_value)]]
    # "Items Discount" reads invoice.discount_amount directly — a stored line-level
    # total, not re-accumulated with a per-unit multiplication. The old renderer did
    # `total_discount += discount * qty`, which is quantity-times too large because
    # discount_amount is already a per-line amount (finance.py subtracts it once).
    rows.append(["Items Discount", _money(invoice.discount_amount)])
    if invoice.is_inter_state:
        rows.append(["IGST", _money(invoice.igst_amount)])
    else:
        rows.append(["SGST", _money(invoice.sgst_amount)])
        rows.append(["CGST", _money(invoice.cgst_amount)])
    rows.append(["Total", f"Rs. {_money(invoice.grand_total)}"])

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


def _build_footer_table(invoice: Invoice, company: CompanyProfile):
    content_width = PAGE_WIDTH - 2 * MARGIN
    supplier_name = invoice.supplier_legal_name or "Ascend Foods"
    left = _p(f"<b>{invoice.invoice_number or ''}</b> ({supplier_name})", STYLE_FOOTER_BOLD)
    right_lines = [
        _p(supplier_name, STYLE_FOOTER),
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


def render_invoice_pdf(invoice: Invoice, company: CompanyProfile | None = None) -> BytesIO:
    """Render a PDF from the `Invoice`/`InvoiceLine` snapshot alone (INV-03).

    `invoice` may be transient — `invoice.id` may still be `None`, and it may not be in
    any Session at all. Reading only `invoice.*` and `invoice.lines[*].*` (plus the
    optional, presentation-only `company` for phone/email/footer text) is what makes
    this callable before `db.add()`, which is exactly where `issue_invoice_for_order`
    calls it to capture `pdf_sha256` before the row becomes immutable.
    """
    if company is None:
        company = CompanyProfile(legal_name="Ascend Foods", invoice_prefix="ASC")

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        # invariant=1 pins the info-dictionary /CreationDate and /ModDate to a fixed
        # sentinel and makes the trailer /ID a deterministic function of content.
        # Verified at planning time: without this flag, two builds of identical content
        # 1.2s apart differ in exactly those bytes; with it, they are byte-identical.
        # Do NOT remove this to "fix" a wrong-looking creation date in a PDF viewer —
        # the legally meaningful date is invoice.invoice_date, printed in the document
        # body below, not this metadata timestamp.
        invariant=1,
    )

    elements = []

    elements.append(_build_header_table(invoice, company))
    elements.append(Spacer(1, 2 * mm))
    elements.append(_build_invoice_info_table(invoice))
    elements.append(Spacer(1, 2 * mm))
    elements.append(_build_address_table(invoice))
    elements.append(Spacer(1, 3 * mm))

    elements.append(_build_items_table(invoice))
    elements.append(Spacer(1, 2 * mm))

    totals_tbl = _build_totals_table(invoice)
    content_width = PAGE_WIDTH - 2 * MARGIN
    wrapper = Table(
        [["", totals_tbl]],
        colWidths=[content_width * 0.5, content_width * 0.5],
    )
    wrapper.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(wrapper)

    elements.append(Spacer(1, 4 * mm))
    elements.append(_build_footer_table(invoice, company))

    footer_text = company.invoice_footer or "This is a computer generated invoice."
    elements.append(Spacer(1, 2 * mm))
    elements.append(_p(footer_text, STYLE_FOOTER))

    doc.build(elements)
    buf.seek(0)
    return buf


def regenerate_invoice_pdf(db: Session, invoice: Invoice) -> tuple[BytesIO, str]:
    """Regenerate a PDF for an already-issued invoice. Goes through the exact same
    `render_invoice_pdf` code path issue-time rendering uses — if the two ever
    diverged, the byte-identity test (app/tests/test_invoice_pdf.py) would prove
    nothing about the real endpoint."""
    company = _company_profile(db)
    buf = render_invoice_pdf(invoice, company)
    filename = f"{invoice.invoice_number}.pdf"
    return buf, filename
