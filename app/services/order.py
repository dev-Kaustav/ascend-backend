from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models import Order, OrderItem, OrderItemBatch, OrderItemTax, SKUBatch, Inventory, InventoryTransaction, Retailer
from app.schemas.order import OrderCreate, StatusUpdate
from app.models.enums import OrderType, OrderStatus, TransactionType
from app.services.finance import calculate_order_totals
from app.services.transactions import transactional_session
from app.core.deps import get_role_value


class InsufficientStockError(Exception):
    pass


class RetailerAccessError(Exception):
    pass

def create_outgoing_order(db: Session, order: OrderCreate, current_user):
    role_value = get_role_value(current_user)
    if role_value == "SALESMAN":
        if not current_user.employee_id:
            raise RetailerAccessError("Salesman missing employee record")
        retailer = db.query(Retailer).filter(Retailer.id == order.retailer_id).first()
        if not retailer or retailer.assigned_salesman_id != current_user.employee_id:
            raise RetailerAccessError("Retailer not assigned to salesman")

    warehouse_id = order.warehouse_id or 1
    with transactional_session(db):
        db_order = Order(
            order_type=OrderType.OUTGOING,
            from_entity_type="WAREHOUSE",
            from_entity_id=warehouse_id,
            to_entity_type="RETAILER",
            to_entity_id=order.retailer_id,
            salesman_id=current_user.employee_id
        )
        db.add(db_order)
        db.flush()

        for item in order.items:
            inventory = db.query(Inventory).filter(
                Inventory.sku_id == item.sku_id,
                Inventory.warehouse_id == warehouse_id
            ).with_for_update().first()
            if not inventory or inventory.total_quantity < item.quantity:
                raise InsufficientStockError("Insufficient stock")

            db_item = OrderItem(
                order_id=db_order.id,
                sku_id=item.sku_id,
                quantity=item.quantity,
                unit_price=item.unit_price,
                discount_amount=item.discount_amount
            )
            db.add(db_item)
            db.flush()

            for tax in item.taxes:
                db_tax = OrderItemTax(
                    order_item_id=db_item.id,
                    tax_type=tax.tax_type,
                    rate=tax.rate
                )
                db.add(db_tax)

            batches = db.query(SKUBatch).filter(
                and_(SKUBatch.sku_id == item.sku_id, SKUBatch.remaining_quantity > 0)
            ).order_by(SKUBatch.expiry_date.is_(None), SKUBatch.expiry_date.asc()).with_for_update().all()
            remaining_qty = item.quantity
            for batch in batches:
                if remaining_qty <= 0:
                    break
                allocate_qty = min(remaining_qty, batch.remaining_quantity)
                order_item_batch = OrderItemBatch(
                    order_item_id=db_item.id,
                    batch_id=batch.id,
                    quantity=allocate_qty
                )
                db.add(order_item_batch)
                batch.remaining_quantity -= allocate_qty
                inventory.total_quantity -= allocate_qty
                transaction = InventoryTransaction(
                    sku_id=item.sku_id,
                    warehouse_id=warehouse_id,
                    batch_id=batch.id,
                    transaction_type=TransactionType.OUT,
                    quantity=allocate_qty
                )
                db.add(transaction)
                remaining_qty -= allocate_qty
            if remaining_qty > 0:
                raise InsufficientStockError("Insufficient stock")
    return db_order

def update_order_status(db: Session, order_id: int, status: StatusUpdate):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise ValueError("Order not found")
    next_status = status.status
    allowed_transitions = {
        OrderStatus.PENDING: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
        OrderStatus.CONFIRMED: {OrderStatus.DELIVERED, OrderStatus.CANCELLED},
        OrderStatus.DELIVERED: set(),
        OrderStatus.CANCELLED: set(),
    }
    if isinstance(next_status, str):
        try:
            next_status = OrderStatus(next_status)
        except ValueError:
            raise ValueError("Invalid status transition")

    if next_status not in allowed_transitions.get(order.status, set()):
        raise ValueError("Invalid status transition")

    if next_status == OrderStatus.CONFIRMED:
        order.status = OrderStatus.CONFIRMED
        if order.order_type == OrderType.OUTGOING and not order.invoice_number:
            order.invoice_number = f"INV-{order.id}"
    elif next_status == OrderStatus.DELIVERED:
        order.status = OrderStatus.DELIVERED
    elif next_status == OrderStatus.CANCELLED:
        order.status = OrderStatus.CANCELLED
        if order.order_type == OrderType.OUTGOING:
            warehouse_id = order.from_entity_id
            for item in order.items:
                for batch in item.order_item_batches:
                    batch.batch.remaining_quantity += batch.quantity
                    inventory = db.query(Inventory).filter(
                        Inventory.sku_id == item.sku_id,
                        Inventory.warehouse_id == warehouse_id
                    ).with_for_update().first()
                    if inventory:
                        inventory.total_quantity += batch.quantity
                    transaction = InventoryTransaction(
                        sku_id=item.sku_id,
                        warehouse_id=warehouse_id,
                        batch_id=batch.batch_id,
                        transaction_type=TransactionType.RETURN,
                        quantity=batch.quantity
                    )
                    db.add(transaction)
    db.commit()
    return order

def get_order_invoice_view(db: Session, order_id: int):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise ValueError("Order not found")
    totals = calculate_order_totals(order)
    return {"order": order, **totals}
