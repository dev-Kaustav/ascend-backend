from decimal import Decimal

from app.models import Order, OrderItem, OrderItemTax, Brand, SKU
from app.models.enums import OrderStatus
from app.services.finance import calculate_order_totals


def test_order_totals_treat_gst_as_included_in_discounted_mrp(db):
    brand = Brand(name="Brand")
    db.add(brand)
    db.flush()
    sku = SKU(name="SKU", brand_id=brand.id)
    db.add(sku)
    db.flush()
    order = Order(
        from_entity_type="WAREHOUSE",
        from_entity_id=1,
        to_entity_type="RETAILER",
        to_entity_id=1,
        status=OrderStatus.DELIVERED,
    )
    db.add(order)
    db.flush()
    item = OrderItem(
        order_id=order.id,
        sku_id=sku.id,
        quantity=1,
        unit_price=540,
        discount_amount=117,
    )
    db.add(item)
    db.flush()
    db.add(OrderItemTax(order_item_id=item.id, tax_type="GST", rate=5))
    db.commit()

    totals = calculate_order_totals(order)

    # Exact Decimal arithmetic: 423 * 5% = 21.15 exactly, no rounding drift,
    # so this is an exact comparison rather than a tolerance-based one now
    # that calculate_order_totals returns Decimal end-to-end.
    assert totals["gst_amount"] == Decimal("21.15")
    assert totals["taxable_value"] == Decimal("401.85")
    assert totals["grand_total"] == Decimal("423.00")


def test_order_totals_with_zero_tax_rows_does_not_raise(db):
    # Regression test for a bug found and fixed in 01-03: an order item with
    # no tax rows at all (a zero-GST SKU with no fallback tax) made
    # sum(tax.rate for tax in item.taxes) default to Python int 0, and
    # 0 / 100 is a float in Python 3, so Decimal * float raised TypeError.
    # Fixed by seeding every sum() with the module-level ZERO = Decimal("0").
    # This test pins that fix so a future refactor of finance.py cannot
    # silently reintroduce it.
    brand = Brand(name="Brand")
    db.add(brand)
    db.flush()
    sku = SKU(name="SKU", brand_id=brand.id)
    db.add(sku)
    db.flush()
    order = Order(
        from_entity_type="WAREHOUSE",
        from_entity_id=1,
        to_entity_type="RETAILER",
        to_entity_id=1,
        status=OrderStatus.DELIVERED,
    )
    db.add(order)
    db.flush()
    item = OrderItem(
        order_id=order.id,
        sku_id=sku.id,
        quantity=1,
        unit_price=100,
        discount_amount=0,
    )
    db.add(item)
    db.commit()
    # No OrderItemTax rows added for this item: item.taxes is empty.

    totals = calculate_order_totals(order)

    assert totals["gst_amount"] == Decimal("0.00")
    assert totals["taxable_value"] == Decimal("100.00")
    assert totals["grand_total"] == Decimal("100.00")
    for value in totals.values():
        assert isinstance(value, Decimal)
