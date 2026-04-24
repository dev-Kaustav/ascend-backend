from io import BytesIO

from sqlalchemy.orm import Session

from app.models import CompanyProfile
from app.services.order import get_order_invoice_view


def _money(value) -> str:
    return f"Rs. {float(value or 0):,.2f}"


def _text(value) -> str:
    raw = "" if value is None else str(value)
    return raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _line(parts) -> str:
    return " | ".join(str(part) for part in parts if part not in (None, ""))


def _company_profile(db: Session) -> CompanyProfile:
    profile = db.query(CompanyProfile).order_by(CompanyProfile.id.asc()).first()
    if profile:
        return profile
    return CompanyProfile(legal_name="Ascend Foods", invoice_prefix="ASC", invoice_next_number=1)


def _draw_text(commands: list[str], x: int, y: int, text: str, size: int = 9, bold: bool = False):
    font = "F2" if bold else "F1"
    commands.append(f"BT /{font} {size} Tf {x} {y} Td ({_text(text)}) Tj ET")


def _draw_rule(commands: list[str], x1: int, y: int, x2: int):
    commands.append(f"{x1} {y} m {x2} {y} l S")


def generate_invoice_pdf(db: Session, order_id: int) -> tuple[BytesIO, str]:
    invoice = get_order_invoice_view(db, order_id)
    order = invoice["order"]
    company = _company_profile(db)
    filename = f"{order.get('invoice_number') or f'order-{order_id}'}.pdf"

    commands = ["0.7 w"]
    y = 800
    _draw_text(commands, 40, y, company.legal_name or "Ascend Foods", 18, True)
    _draw_text(commands, 440, y, "TAX INVOICE", 16, True)
    y -= 18
    _draw_text(commands, 40, y, _line([company.address_line1, company.address_line2]), 8)
    y -= 12
    _draw_text(commands, 40, y, _line([company.city, company.state, company.pincode]), 8)
    y -= 12
    _draw_text(commands, 40, y, _line([f"GSTIN: {company.gstin}" if company.gstin else None, company.phone, company.email]), 8)
    _draw_rule(commands, 40, y - 14, 555)

    y -= 42
    _draw_text(commands, 40, y, "Bill To", 10, True)
    _draw_text(commands, 320, y, "Invoice Details", 10, True)
    y -= 16
    _draw_text(commands, 40, y, order.get("retailer_name") or f"Retailer {order.get('to_entity_id')}", 9, True)
    _draw_text(commands, 320, y, f"Invoice No: {order.get('invoice_number') or '-'}", 9)
    y -= 13
    _draw_text(commands, 40, y, _line([order.get("retailer_state"), f"GSTIN: {order.get('retailer_gst_number')}" if order.get("retailer_gst_number") else None]), 8)
    _draw_text(commands, 320, y, f"Order No: {order.get('id')}", 9)
    y -= 13
    _draw_text(commands, 40, y, f"Warehouse: {order.get('warehouse_name') or '-'}", 8)
    _draw_text(commands, 320, y, f"Status: {order.get('status')}", 9)
    y -= 13
    _draw_text(commands, 40, y, f"Warehouse State: {order.get('warehouse_state') or '-'}", 8)
    _draw_text(commands, 320, y, f"Payment: {order.get('payment_status')}", 9)

    y -= 30
    _draw_rule(commands, 40, y + 16, 555)
    headers = [("SKU", 42), ("HSN", 210), ("Qty", 270), ("MRP", 315), ("Disc", 370), ("Taxable", 425), ("GST", 485), ("Total", 525)]
    for label, x in headers:
        _draw_text(commands, x, y, label, 8, True)
    _draw_rule(commands, 40, y - 8, 555)
    y -= 24
    for item in order.get("items", []):
        taxes = ", ".join(f"{tax.get('tax_type')} {tax.get('rate'):.2f}%" for tax in item.get("taxes", []))
        name = (item.get("sku_name") or f"SKU {item.get('sku_id')}")[:28]
        _draw_text(commands, 42, y, name, 8)
        _draw_text(commands, 210, y, item.get("hsn_code") or "-", 8)
        _draw_text(commands, 270, y, f"{float(item.get('quantity') or 0):.2f}", 8)
        _draw_text(commands, 315, y, _money(item.get("unit_price")), 8)
        _draw_text(commands, 370, y, _money(item.get("discount_amount")), 8)
        _draw_text(commands, 425, y, _money(item.get("taxable_value")), 8)
        _draw_text(commands, 485, y, _money(item.get("gst_amount")), 8)
        _draw_text(commands, 525, y, _money(item.get("line_total")), 8)
        y -= 16
        if taxes:
            _draw_text(commands, 42, y, taxes, 7)
            y -= 12
        if y < 160:
            break

    y = max(y - 10, 150)
    _draw_rule(commands, 320, y + 12, 555)
    summary = [
        ("Taxable Value", invoice.get("taxable_value")),
        ("GST", invoice.get("gst_amount")),
        ("Invoice Total", invoice.get("grand_total")),
        ("Payment Pending", order.get("pending_amount")),
    ]
    for label, value in summary:
        _draw_text(commands, 360, y, label, 9, label == "Invoice Total")
        _draw_text(commands, 485, y, _money(value), 9, label == "Invoice Total")
        y -= 16

    footer = company.invoice_footer or "This is a computer generated invoice."
    _draw_rule(commands, 40, 86, 555)
    _draw_text(commands, 40, 66, footer, 8)
    _draw_text(commands, 420, 66, f"For {company.legal_name or 'Ascend Foods'}", 8, True)

    content = "\n".join(commands).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"\nendstream",
    ]

    pdf = BytesIO()
    pdf.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(pdf.tell())
        pdf.write(f"{index} 0 obj\n".encode("ascii"))
        pdf.write(obj)
        pdf.write(b"\nendobj\n")
    xref_at = pdf.tell()
    pdf.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.write(f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF".encode("ascii"))
    pdf.seek(0)
    return pdf, filename
