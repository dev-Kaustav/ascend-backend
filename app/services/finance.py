from decimal import Decimal, ROUND_HALF_UP

from app.models import Order, CreditNote


def _round_money(value) -> Decimal:
    d = value if isinstance(value, Decimal) else Decimal(str(value or 0))
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _order_item_taxable_value(item) -> Decimal:
    discount_amount = item.discount_amount or 0
    inclusive_value = max(item.quantity * item.unit_price - discount_amount, 0)
    return _round_money(max(inclusive_value - _order_item_tax_amount(item), 0))


def _order_item_tax_amount(item) -> Decimal:
    inclusive_value = max(item.quantity * item.unit_price - (item.discount_amount or 0), 0)
    total_rate = sum(tax.rate for tax in item.taxes)
    return _round_money(inclusive_value * (total_rate / 100))


def calculate_order_item_totals(item) -> dict:
    gst_amount = _order_item_tax_amount(item)
    taxable_value = _order_item_taxable_value(item)
    line_total = _round_money(max((item.quantity or 0) * (item.unit_price or 0) - (item.discount_amount or 0), 0))
    return {
        "taxable_value": taxable_value,
        "gst_amount": gst_amount,
        "line_total": line_total,
    }


def calculate_order_totals(order: Order) -> dict:
    taxable_value = 0
    gst_amount = 0
    for item in order.items:
        item_totals = calculate_order_item_totals(item)
        taxable_value += item_totals["taxable_value"]
        gst_amount += item_totals["gst_amount"]
    taxable_value = _round_money(taxable_value)
    gst_amount = _round_money(gst_amount)
    grand_total = _round_money(taxable_value + gst_amount)
    subtotal = grand_total
    return {
        "taxable_value": taxable_value,
        "gst_amount": gst_amount,
        "subtotal": subtotal,
        "grand_total": grand_total,
    }


def _tax_rate_by_sku(order: Order) -> dict[int, float]:
    rates = {}
    for item in order.items:
        rates[item.sku_id] = sum(tax.rate for tax in item.taxes)
    return rates


def calculate_credit_note_totals(credit_note: CreditNote) -> dict:
    order = credit_note.order
    rates = _tax_rate_by_sku(order) if order else {}
    taxable_value = 0
    gst_amount = 0
    for item in credit_note.items:
        inclusive_value = item.quantity * item.unit_price
        item_gst = _round_money(inclusive_value * (rates.get(item.sku_id, 0) / 100))
        taxable_value += _round_money(max(inclusive_value - item_gst, 0))
        gst_amount += item_gst
    taxable_value = _round_money(taxable_value)
    gst_amount = _round_money(gst_amount)
    grand_total = _round_money(taxable_value + gst_amount)
    subtotal = grand_total
    return {
        "taxable_value": taxable_value,
        "gst_amount": gst_amount,
        "subtotal": subtotal,
        "grand_total": grand_total,
    }


def calculate_order_outstanding(order: Order) -> Decimal:
    order_totals = calculate_order_totals(order)
    # Payments appended to `order.payments` in the same transaction (e.g.
    # create_payment, before flush) may still hold the raw Python value
    # assigned at construction rather than the Decimal SQLAlchemy would
    # return after a round-trip through the Numeric column. Route each
    # through _round_money so the accumulation is Decimal-native regardless
    # of flush state.
    payments_total = sum(
        (_round_money(payment.amount) for payment in order.payments), Decimal("0")
    )
    credit_total = 0
    for credit_note in order.credit_notes:
        if getattr(credit_note, "applies_to_outstanding", True):
            credit_total += calculate_credit_note_totals(credit_note)["grand_total"]
    outstanding = order_totals["grand_total"] - payments_total - credit_total
    return _round_money(max(outstanding, 0))
