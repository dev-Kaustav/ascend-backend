import pytest

from app.models import Order, OrderItem, OrderItemTax, Account, Brand, SKU
from app.models.enums import OrderStatus
from app.schemas.accounting import PaymentCreate, CreditNoteCreate, CreditNoteItemCreate
from app.services.accounting import create_payment, create_credit_note


def _create_order_with_item(db, quantity=1):
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
        status=OrderStatus.CONFIRMED
    )
    db.add(order)
    db.flush()
    item = OrderItem(
        order_id=order.id,
        sku_id=sku.id,
        quantity=quantity,
        unit_price=100,
        discount_amount=0
    )
    db.add(item)
    db.flush()
    tax = OrderItemTax(order_item_id=item.id, tax_type="GST", rate=0)
    db.add(tax)
    db.commit()
    return order, sku.id


def test_payment_idempotency(db):
    order, _ = _create_order_with_item(db)
    payment = PaymentCreate(amount=50, transaction_reference="txn-123")
    first = create_payment(db, order.id, payment)
    second = create_payment(db, order.id, payment)
    assert first.id == second.id
    assert db.query(Account).filter(Account.order_id == order.id).count() == 1


def test_credit_note_quantity_limits(db):
    order, sku_id = _create_order_with_item(db, quantity=2)
    order.status = OrderStatus.DELIVERED
    db.commit()
    credit_note = CreditNoteCreate(
        order_id=order.id,
        items=[CreditNoteItemCreate(sku_id=sku_id, quantity=3, unit_price=100)],
        restock=False
    )
    with pytest.raises(ValueError):
        create_credit_note(db, credit_note)
