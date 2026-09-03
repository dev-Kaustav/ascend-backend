from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from PIL import Image as PILImage
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
STYLE_SMALL_RIGHT = ParagraphStyle("smallRight", fontName=FONT, fontSize=7, leading=9, alignment=2)
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

    # One row, two cells. The order id used to sit here but it is an internal primary key
    # with no meaning to the buyer or to a CA, and it is not a GST invoice field — the
    # invoice number is the document's identity. The order is still reachable internally
    # via invoices.order_id.
    data = [
        [
            _p(f"INVOICE NUMBER: <b>{invoice.invoice_number or '-'}</b>", STYLE_NORMAL),
            _p(f"Invoice Date: {invoice_date}", STYLE_NORMAL),
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


def _discount_cell(line) -> str:
    """Discount as `amount (percent)`, e.g. `30.00 (10%)`.

    The percentage is derived from the line's own gross (unit_rate x quantity) rather than
    stored, because no discount-percent column exists — the order form lets a user enter
    either a percent or an amount and only the resolved amount is persisted.
    """
    discount = line.discount_amount or Decimal(0)
    if not discount:
        return "0.00"
    gross = (Decimal(str(line.unit_rate or 0))) * (line.quantity or 0)
    if gross <= 0:
        return _money(discount)
    percent = (Decimal(str(discount)) / gross) * 100
    # Whole percentages are the common case and reading "10%" beats "10.00%"; keep one
    # decimal otherwise so 12.5% does not silently round to 13%.
    text = f"{percent:.0f}" if percent == percent.to_integral_value() else f"{percent:.1f}"
    return f"{_money(discount)} ({text}%)"


def _build_items_table(invoice: Invoice):
    """Built from `invoice.lines` alone — every tax figure is a stored column, never
    recomputed. There used to be an `_extract_taxes` helper here that independently
    computed `taxable * rate / 100`, a second tax computation disagreeing with
    `finance.py`'s `inclusive_value * rate / 100` (a direct D-03 violation). It is gone;
    the components below are read straight off the InvoiceLine snapshot."""
    content_width = PAGE_WIDTH - 2 * MARGIN
    inter_state = invoice.is_inter_state

    # Every column except the description is fixed; the description absorbs the remainder
    # so the widths sum to exactly content_width. The previous fractional widths summed to
    # ~466pt against a 553pt content width, leaving the items table visibly narrower than
    # the header, totals and footer boxes it sits between.
    # Widths are measured against the real content width (510pt at A4 with 15mm margins),
    # every column except the description fixed, the description absorbing the remainder so
    # the row sums to exactly content_width. The previous fractional widths summed to ~466pt
    # and left the items table visibly narrower than the boxes above and below it.
    if inter_state:
        header = ["Sr.", "Item Description", "HSN", "Qty", "Unit Rate", "Disc",
                  "Taxable", "IGST %", "IGST", "Amount"]
        fixed = [18, 40, 20, 40, 52, 46, 30, 44, 48]
    else:
        header = ["Sr.", "Item Description", "HSN", "Qty", "Unit Rate", "Disc",
                  "Taxable", "SGST %", "SGST", "CGST %", "CGST", "Amount"]
        fixed = [18, 40, 20, 40, 52, 46, 28, 40, 28, 40, 48]
    description_width = content_width - sum(fixed)
    col_widths = [fixed[0], description_width] + fixed[1:]

    data = [header]

    for line in invoice.lines:
        qty = line.quantity or 0
        row = [
            str(line.line_number),
            _p(line.description or f"SKU {line.sku_id}", STYLE_SMALL),
            line.hsn_code or "-",
            str(qty),
            _money(line.unit_rate),
            _p(_discount_cell(line), STYLE_SMALL_RIGHT),
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

    # No "Items Discount" row. Sub Total is the sum of line taxable values, and
    # finance._order_item_taxable_value already subtracts the discount before tax:
    # taxable = (qty * unit_rate - discount) - tax. Listing the discount again in a column
    # that reads as a running subtraction implies it is deducted twice. The per-line
    # discount is shown in the items table's Disc column, and invoice.discount_amount is
    # still stored and still exported for GSTR-1 — it is only absent from this summary.
    rows = [["Sub Total", _money(invoice.taxable_value)]]
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


def _payment_detail_rows(invoice: Invoice):
    """Bank rows for the payment block, read from the invoice's own frozen columns.

    Never from the live CompanyProfile: these are snapshotted at issuance precisely so a
    later bank change cannot alter an already-issued document (and break pdf_sha256).
    """
    return [
        (label, value)
        for label, value in (
            ("Bank", invoice.supplier_bank_name),
            ("Account Name", invoice.supplier_bank_account_name),
            ("Account No.", invoice.supplier_bank_account_number),
            ("IFSC", invoice.supplier_bank_ifsc),
            ("Branch", invoice.supplier_bank_branch),
        )
        if value
    ]


def _qr_flowable(invoice: Invoice):
    """The frozen payment QR as a reportlab Image, or None.

    Reads invoice.payment_qr_image, which issue_invoice_for_order assigns as an ORM object
    so this works while the invoice is still transient. A corrupt or unreadable image is
    skipped rather than raised: a bad QR must never make an invoice unrenderable, because
    the document is a legal record and the QR is a convenience.
    """
    image = getattr(invoice, "payment_qr_image", None)
    if image is None or not getattr(image, "image_bytes", None):
        return None
    # Validate eagerly with PIL. reportlab's Image is lazy — it does not touch the bytes
    # until doc.build(), so wrapping the constructor in try/except catches nothing and a
    # corrupt stored image would raise deep inside the build and make the invoice
    # unrenderable. PIL is a hard dependency of reportlab, so this adds no requirement.
    # Validate eagerly with PIL. reportlab's Image is lazy — it does not touch the bytes
    # until doc.build(), so wrapping the constructor in try/except catches nothing and a
    # corrupt stored image would raise deep inside the build and make the invoice
    # unrenderable. PIL is a hard dependency of reportlab, so this adds no requirement.
    try:
        PILImage.open(BytesIO(image.image_bytes)).verify()
    except Exception:
        return None
    # Drawn at full width with no added margin. The QR spec wants a ~4-module quiet zone,
    # and the surrounding table cell's 6pt padding on a white page already provides it —
    # verified by rasterising the finished invoice at 300 DPI and scanning it. Padding the
    # image here instead would only shrink the printed code inside the same 26mm box.
    return Image(BytesIO(image.image_bytes), width=26 * mm, height=26 * mm)


def _build_payment_details_table(invoice: Invoice):
    """Bank details on the left, payment QR on the right. Returns None when the invoice
    froze neither, so an invoice issued before any payment details existed is unchanged."""
    rows = _payment_detail_rows(invoice)
    qr = _qr_flowable(invoice)
    if not rows and qr is None:
        return None

    content_width = PAGE_WIDTH - 2 * MARGIN
    left = [_p("Payment Details", STYLE_LABEL)]
    for label, value in rows:
        left.append(_p(f"{label}: {value}", STYLE_SMALL))
    if not rows:
        left.append(_p("Scan to pay", STYLE_SMALL))

    right = qr if qr is not None else _p("")

    tbl = Table([[left, right]], colWidths=[content_width * 0.7, content_width * 0.3])
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.Color(0.7, 0.7, 0.7)),
        ("VALIGN", (0, 0), (0, -1), "TOP"),
        ("VALIGN", (1, 0), (1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
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
    # Zero padding, or the nested totals table (exactly half the content width) overflows
    # its wrapper cell by reportlab's default 6pt-per-side padding and its right edge sits
    # 12pt past the items table's. The wrapper exists only to right-align, not to inset.
    wrapper.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    elements.append(wrapper)

    payment_tbl = _build_payment_details_table(invoice)
    if payment_tbl is not None:
        elements.append(Spacer(1, 3 * mm))
        elements.append(payment_tbl)

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
