import pytest

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

    assert totals["gst_amount"] == pytest.approx(21.15)
    assert totals["taxable_value"] == pytest.approx(401.85)
    assert totals["grand_total"] == pytest.approx(423)
