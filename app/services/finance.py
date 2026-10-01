from decimal import Decimal, ROUND_HALF_UP

from app.models import Order, CreditNote

# Rounding policy for this module: quantize to 2dp with ROUND_HALF_UP (not
# Decimal's default ROUND_HALF_EVEN / banker's rounding) so 0.125 rounds to
# 0.13, matching what an Indian tax invoice / a human reader expects.
TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0")


def _round_money(value) -> Decimal:
    if value is None:
        return ZERO.quantize(TWO_PLACES)
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return d.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _as_decimal(value) -> Decimal:
    if value is None:
        return ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


def inclusive_tax_amount(inclusive_value, total_rate) -> Decimal:
    """GST on a GST-inclusive amount at `total_rate` percent.

    The single site of the formula (D-33): order item lines and the delivery charge both call it,
    so a chartered-accountant correction (STATE.md flags whether this should back-compute the tax
    out of an inclusive value) changes one function.
    """
    return _round_money(_as_decimal(inclusive_value) * (_as_decimal(total_rate) / 100))


def _order_item_taxable_value(item) -> Decimal:
    discount_amount = item.discount_amount or ZERO
    inclusive_value = max(item.quantity * item.unit_price - discount_amount, ZERO)
    return _round_money(max(inclusive_value - _order_item_tax_amount(item), ZERO))


def _order_item_tax_amount(item) -> Decimal:
    inclusive_value = max(item.quantity * item.unit_price - (item.discount_amount or ZERO), ZERO)
    total_rate = sum((tax.rate for tax in item.taxes), ZERO)
    return inclusive_tax_amount(inclusive_value, total_rate)


def calculate_order_item_totals(item) -> dict:
    gst_amount = _order_item_tax_amount(item)
    taxable_value = _order_item_taxable_value(item)
    line_total = _round_money(max((item.quantity or 0) * (item.unit_price or ZERO) - (item.discount_amount or ZERO), ZERO))
    return {
        "taxable_value": taxable_value,
        "gst_amount": gst_amount,
        "line_total": line_total,
    }


def compute_delivery_charge(cart_value, min_order_value, percent) -> Decimal:
    """The GST-inclusive delivery charge for a cart (D-04, STORE-11): `percent` of the cart value,
    rounded half-up to 2 dp, when the cart is strictly below a positive minimum; otherwise 0.
    A minimum of 0 never charges."""
    cart_value = _as_decimal(cart_value)
    min_order_value = _as_decimal(min_order_value)
    if min_order_value <= ZERO or cart_value >= min_order_value:
        return _round_money(ZERO)
    return _round_money(cart_value * _as_decimal(percent) / 100)


def delivery_charge_totals(order) -> dict:
    """Taxable value, GST and line total of an order's delivery charge. The charge is GST-inclusive,
    so taxable_value + gst_amount == line_total exactly. Zeros when there is no charge, and for
    transient objects that carry no delivery attributes."""
    charge = _round_money(getattr(order, "delivery_charge", None))
    if charge <= ZERO:
        zero = _round_money(ZERO)
        return {"taxable_value": zero, "gst_amount": zero, "line_total": zero}
    gst_amount = inclusive_tax_amount(charge, getattr(order, "delivery_charge_gst_rate", None))
    return {
        "taxable_value": _round_money(charge - gst_amount),
        "gst_amount": gst_amount,
        "line_total": charge,
    }


def calculate_order_totals(order: Order) -> dict:
    taxable_value = ZERO
    gst_amount = ZERO
    for item in order.items:
        item_totals = calculate_order_item_totals(item)
        taxable_value += item_totals["taxable_value"]
        gst_amount += item_totals["gst_amount"]
    delivery = delivery_charge_totals(order)
    taxable_value += delivery["taxable_value"]
    gst_amount += delivery["gst_amount"]
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


def _tax_rate_by_sku(order: Order) -> dict[int, Decimal]:
    rates = {}
    for item in order.items:
        rates[item.sku_id] = sum((tax.rate for tax in item.taxes), ZERO)
    return rates


def calculate_credit_note_totals(credit_note: CreditNote) -> dict:
    order = credit_note.order
    rates = _tax_rate_by_sku(order) if order else {}
    taxable_value = ZERO
    gst_amount = ZERO
    for item in credit_note.items:
        inclusive_value = item.quantity * item.unit_price
        item_gst = _round_money(inclusive_value * (rates.get(item.sku_id, ZERO) / 100))
        taxable_value += _round_money(max(inclusive_value - item_gst, ZERO))
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
        (_round_money(payment.amount) for payment in order.payments), ZERO
    )
    credit_total = ZERO
    for credit_note in order.credit_notes:
        if getattr(credit_note, "applies_to_outstanding", True):
            credit_total += calculate_credit_note_totals(credit_note)["grand_total"]
    outstanding = order_totals["grand_total"] - payments_total - credit_total
    return _round_money(max(outstanding, ZERO))
