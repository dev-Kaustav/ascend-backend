"""Turns a dispatched order into an immutable Invoice snapshot (INV-01, INV-02, INV-05).

The only source of an invoice line's tax figures is
`finance.calculate_order_item_totals`; this module allocates that already-computed
number across an item's CGST/SGST/IGST rows, it does not compute tax itself (D-03).
Nothing in the codebase calls this module yet — plan 02-03 wires `issue_invoice_for_order`
into the order dispatch transition.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models import CompanyProfile, Invoice, InvoiceLine, Retailer, SKU, Warehouse
from app.models.enums import InvoiceStatus, InvoiceType, SupplyType
from app.models.invoice import DEFAULT_UQC
from app.services.finance import calculate_order_item_totals, _round_money

ZERO = Decimal("0")

# tax_type -> InvoiceLine column prefix. Only these three families have a component
# column; CESS has no source anywhere in this codebase today (see _tax_components_for_item).
_KNOWN_TAX_PREFIXES = {"CGST": "cgst", "SGST": "sgst", "IGST": "igst", "CESS": "cess"}


def _next_invoice_serial(db: Session) -> int:
    """Draw the next invoice serial.

    Production path: `nextval('invoice_number_seq')` (migration 0045). Atomic, no
    collision-check query, no retry loop — a collision is a bug to surface, not
    something to paper over (D-04).

    The `!= "postgresql"` branch below only ever fires against the SQLite test engine
    (`app/tests/conftest.py`), which has no sequence object. It is NOT the production
    path; it exists purely so this module is importable and testable without Postgres.
    """
    if db.get_bind().dialect.name != "postgresql":
        max_serial = db.query(func.max(Invoice.invoice_serial)).scalar()
        return int(max_serial or 0) + 1
    return db.execute(text("SELECT nextval('invoice_number_seq')")).scalar_one()


def _get_or_create_company_profile(db: Session) -> CompanyProfile:
    """Mirror app/services/order.py:_get_company_profile_for_update minus the row lock —
    there is no longer a counter on this row to protect (D-04)."""
    profile = db.query(CompanyProfile).order_by(CompanyProfile.id.asc()).first()
    if profile:
        return profile
    profile = CompanyProfile(legal_name="Ascend Foods", invoice_prefix="ASC")
    db.add(profile)
    db.flush()
    return profile


def next_invoice_number(db: Session) -> tuple[str, int]:
    """Return (formatted_number, serial). Does not read or write
    `company_profile.invoice_next_number` — that column stops being the source of
    truth this phase (D-04); it is dropped in plan 02-03."""
    profile = _get_or_create_company_profile(db)
    prefix = (profile.invoice_prefix or "ASC").strip().upper() or "ASC"
    serial = _next_invoice_serial(db)
    return f"{prefix}{serial:06d}", serial


def _tax_components_for_item(item, gst_amount: Decimal) -> dict[str, Decimal]:
    """Allocate the already-computed `gst_amount` across an item's OrderItemTax rows.

    This function never computes tax — it splits a total that
    `finance.calculate_order_item_totals` already returned. Every row except the last
    known-family row gets a proportional share (`gst_amount * rate / total_rate`); the
    last known-family row absorbs the remainder, so the components sum to `gst_amount`
    exactly with no rounding drift.
    """
    components = {
        "cgst_rate": ZERO, "cgst_amount": ZERO,
        "sgst_rate": ZERO, "sgst_amount": ZERO,
        "igst_rate": ZERO, "igst_amount": ZERO,
        "cess_rate": ZERO, "cess_amount": ZERO,
    }
    rows = [((tax.tax_type or "").upper(), tax.rate or ZERO) for tax in item.taxes]
    # Seeded with Decimal ZERO, not int 0 — Phase 1 (01-03) hit a real TypeError from an
    # int-seeded sum landing in a Decimal / float division on the zero-tax-rows path.
    total_rate = sum((rate for _, rate in rows), ZERO)
    # Rows outside the three known families (there are none in this codebase today,
    # per app/services/order.py:_tax_rows_for_item) are not written to a component —
    # there is no bucket for them — but their rate is still included in total_rate
    # above, so their implicit share folds into the last known row's remainder rather
    # than silently vanishing.
    known_rows = [(_KNOWN_TAX_PREFIXES[tax_type], rate) for tax_type, rate in rows if tax_type in _KNOWN_TAX_PREFIXES]
    if total_rate == ZERO or not known_rows:
        return components

    allocated = ZERO
    last_index = len(known_rows) - 1
    for index, (prefix, rate) in enumerate(known_rows):
        components[f"{prefix}_rate"] = rate
        if index < last_index:
            amount = _round_money(gst_amount * rate / total_rate)
            components[f"{prefix}_amount"] = amount
            allocated += amount
        else:
            components[f"{prefix}_amount"] = _round_money(gst_amount - allocated)
    return components


def issue_invoice_for_order(
    db: Session,
    order,
    invoice_number: str | None = None,
    invoice_date: datetime | None = None,
) -> Invoice:
    """Turn a dispatched Order into a persisted, immutable Invoice + InvoiceLines.

    Idempotent: a second call on an order that already has an invoice returns the
    existing invoice and consumes no sequence value — a re-dispatch must not mint a
    second legal document.
    """
    if order.invoice is not None:
        return order.invoice

    # Local import: app/services/order.py does not import this module today, but plan
    # 02-03 wires issue_invoice_for_order into order.py's dispatch transition, which
    # would make a module-level `from app.services.order import _is_inter_state` here
    # a circular import (order.py -> invoice.py -> order.py). Importing inside the
    # function sidesteps that in advance rather than fixing it later.
    from app.services.order import _is_inter_state

    profile = _get_or_create_company_profile(db)
    warehouse = db.query(Warehouse).filter(Warehouse.id == order.from_entity_id).first()
    retailer = db.query(Retailer).filter(Retailer.id == order.to_entity_id).first()
    inter_state = _is_inter_state(warehouse, retailer)

    buyer_name = retailer.name if retailer and retailer.name else f"Retailer {order.to_entity_id}"
    buyer_gstin = getattr(retailer, "gst_number", None)
    buyer_state = getattr(retailer, "state", None)
    buyer_address = ", ".join(filter(None, [
        getattr(retailer, "address_line1", None),
        getattr(retailer, "address_line2", None),
    ])) or None
    buyer_pincode = str(retailer.pincode) if retailer and retailer.pincode is not None else None

    supplier_address = ", ".join(filter(None, [profile.address_line1, profile.address_line2])) or None
    # Goods leave the warehouse, so its state is the fallback when the profile's own
    # state is NULL (true in the dev database today; every warehouse has a state).
    supplier_state = profile.state or getattr(warehouse, "state", None)

    invoice = Invoice(
        order=order,
        invoice_date=invoice_date or datetime.utcnow(),
        status=InvoiceStatus.ISSUED.value,
        invoice_type=InvoiceType.B2B.value if buyer_gstin else InvoiceType.B2C.value,
        supply_type=SupplyType.REGULAR.value,
        reverse_charge=False,
        # Place of supply for goods is the recipient's location.
        place_of_supply=buyer_state,
        is_inter_state=inter_state,
        supplier_legal_name=profile.legal_name or "Ascend Foods",
        supplier_gstin=profile.gstin,
        supplier_state=supplier_state,
        supplier_address=supplier_address,
        supplier_pincode=profile.pincode,
        buyer_name=buyer_name,
        buyer_gstin=buyer_gstin,
        buyer_state=buyer_state,
        buyer_address=buyer_address,
        buyer_pincode=buyer_pincode,
        taxable_value=ZERO,
        discount_amount=ZERO,
        cgst_amount=ZERO,
        sgst_amount=ZERO,
        igst_amount=ZERO,
        cess_amount=ZERO,
        total_tax_amount=ZERO,
        grand_total=ZERO,
    )

    if invoice_number:
        # Externally assigned number — historical import (plan 02-03's Excel path).
        # No sequence value consumed; invoice_serial stays NULL by design.
        invoice.invoice_number = invoice_number
        invoice.invoice_serial = None
    else:
        number, serial = next_invoice_number(db)
        invoice.invoice_number = number
        invoice.invoice_serial = serial

    sku_ids = [item.sku_id for item in order.items]
    sku_map = {sku.id: sku for sku in db.query(SKU).filter(SKU.id.in_(sku_ids)).all()} if sku_ids else {}

    taxable_total = ZERO
    discount_total = ZERO
    cgst_total = ZERO
    sgst_total = ZERO
    igst_total = ZERO
    cess_total = ZERO
    tax_total = ZERO
    grand_total = ZERO

    # Stable, id-ordered — line_number must be reproducible across reads (PDF
    # byte-identity in plan 02-04 depends on it).
    for line_number, item in enumerate(sorted(order.items, key=lambda i: i.id), start=1):
        # The single tax entry point (D-03) — every money figure on this line traces
        # back to this one call.
        totals = calculate_order_item_totals(item)
        components = _tax_components_for_item(item, totals["gst_amount"])
        sku = sku_map.get(item.sku_id)
        line = InvoiceLine(
            line_number=line_number,
            sku_id=item.sku_id,
            description=sku.name if sku and sku.name else f"SKU {item.sku_id}",
            hsn_code=sku.hsn_code if sku else None,
            quantity=item.quantity,
            uqc=DEFAULT_UQC,
            unit_rate=_round_money(item.unit_price),
            discount_amount=_round_money(item.discount_amount),
            taxable_value=totals["taxable_value"],
            total_tax_amount=totals["gst_amount"],
            line_total=totals["line_total"],
            **components,
        )
        invoice.lines.append(line)

        taxable_total += totals["taxable_value"]
        discount_total += _round_money(item.discount_amount)
        cgst_total += components["cgst_amount"]
        sgst_total += components["sgst_amount"]
        igst_total += components["igst_amount"]
        cess_total += components["cess_amount"]
        tax_total += totals["gst_amount"]
        grand_total += totals["line_total"]

    # Rolled up from the stored lines, not re-invoked from calculate_order_totals(order)
    # — a reader adding up the printed lines gets the printed total.
    invoice.taxable_value = _round_money(taxable_total)
    invoice.discount_amount = _round_money(discount_total)
    invoice.cgst_amount = _round_money(cgst_total)
    invoice.sgst_amount = _round_money(sgst_total)
    invoice.igst_amount = _round_money(igst_total)
    invoice.cess_amount = _round_money(cess_total)
    invoice.total_tax_amount = _round_money(tax_total)
    invoice.grand_total = _round_money(grand_total)

    db.add(invoice)
    db.flush()
    return invoice
