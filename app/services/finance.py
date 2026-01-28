from app.models import Order, CreditNote


def _order_item_taxable_value(item) -> float:
    discount_amount = item.discount_amount or 0
    return max(item.quantity * item.unit_price - discount_amount, 0)


def _order_item_tax_amount(item) -> float:
    taxable_value = _order_item_taxable_value(item)
    total_rate = sum(tax.rate for tax in item.taxes)
    return taxable_value * (total_rate / 100)


def calculate_order_totals(order: Order) -> dict:
    taxable_value = 0
    gst_amount = 0
    for item in order.items:
        taxable_value += _order_item_taxable_value(item)
        gst_amount += _order_item_tax_amount(item)
    subtotal = taxable_value + gst_amount
    grand_total = subtotal
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
        taxable_value += item.quantity * item.unit_price
        gst_amount += (item.quantity * item.unit_price) * (rates.get(item.sku_id, 0) / 100)
    subtotal = taxable_value + gst_amount
    grand_total = subtotal
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
    return max(outstanding, 0)
