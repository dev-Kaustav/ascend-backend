from app.models import Order, CreditNote


def _round_money(value: float) -> float:
    return round(float(value or 0), 2)


def _order_item_taxable_value(item) -> float:
    discount_amount = item.discount_amount or 0
    inclusive_value = max(item.quantity * item.unit_price - discount_amount, 0)
    return _round_money(max(inclusive_value - _order_item_tax_amount(item), 0))


def _order_item_tax_amount(item) -> float:
    inclusive_value = max(item.quantity * item.unit_price - (item.discount_amount or 0), 0)
    total_rate = sum(tax.rate for tax in item.taxes)
    return _round_money(inclusive_value * (total_rate / 100))


def calculate_order_totals(order: Order) -> dict:
    taxable_value = 0
    gst_amount = 0
    for item in order.items:
        taxable_value += _order_item_taxable_value(item)
        gst_amount += _order_item_tax_amount(item)
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


def calculate_order_outstanding(order: Order) -> float:
    order_totals = calculate_order_totals(order)
    payments_total = sum(payment.amount for payment in order.payments)
    credit_total = 0
    for credit_note in order.credit_notes:
        if getattr(credit_note, "applies_to_outstanding", True):
            credit_total += calculate_credit_note_totals(credit_note)["grand_total"]
    outstanding = order_totals["grand_total"] - payments_total - credit_total
    return _round_money(max(outstanding, 0))
