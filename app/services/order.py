from datetime import datetime, date, time as dtime
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models import (
    Order,
    OrderItem,
    OrderItemBatch,
    OrderItemTax,
    SKUBatch,
    Inventory,
    InventoryTransaction,
    Retailer,
    Account,
    Employee,
)
from app.schemas.order import OrderCreate, StatusUpdate
from app.models.enums import OrderStatus, TransactionType, PaymentStatus
from app.services.finance import calculate_order_totals
from app.services.transactions import transactional_session
from app.core.deps import get_role_value


class InsufficientStockError(Exception):
    pass


class RetailerAccessError(Exception):
    pass

def _is_outgoing_order(order: Order) -> bool:
    return order.from_entity_type == "WAREHOUSE" and order.to_entity_type == "RETAILER"

def _allocate_inventory_for_order(db: Session, order: Order):
    warehouse_id = order.from_entity_id
    for item in order.items:
        allocated_qty = sum(batch.quantity for batch in item.order_item_batches)
        if allocated_qty >= item.quantity:
            continue
        inventory = db.query(Inventory).filter(
            Inventory.sku_id == item.sku_id,
            Inventory.warehouse_id == warehouse_id
        ).with_for_update().first()
        if not inventory or inventory.total_quantity < item.quantity:
            raise InsufficientStockError("Insufficient stock")

        batches = (
            db.query(SKUBatch)
            .filter(
                and_(
                    SKUBatch.sku_id == item.sku_id,
                    SKUBatch.warehouse_id == warehouse_id,
                    SKUBatch.remaining_quantity > 0,
                )
            )
            .order_by(SKUBatch.expiry_date.is_(None), SKUBatch.expiry_date.asc())
            .with_for_update()
            .all()
        )
        remaining_qty = item.quantity
        for batch in batches:
            if remaining_qty <= 0:
                break
            allocate_qty = min(remaining_qty, batch.remaining_quantity)
            order_item_batch = OrderItemBatch(
                order_item_id=item.id,
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

def create_outgoing_order(db: Session, order: OrderCreate, current_user):
    role_value = get_role_value(current_user)
    salesman_id = None
    if role_value == "SALESMAN":
        if not current_user.employee_id:
            raise RetailerAccessError("Salesman missing employee record")
        retailer = db.query(Retailer).filter(Retailer.id == order.retailer_id).first()
        if not retailer or retailer.assigned_salesman_id != current_user.employee_id:
            raise RetailerAccessError("Retailer not assigned to salesman")
        salesman_id = current_user.employee_id
    elif order.salesman_id:
        salesman_id = order.salesman_id

    warehouse_id = order.warehouse_id or 1
    payment_mode = (order.payment_mode or "").strip().upper()
    payment_amount = order.payment_amount

    created_at_override = None
    if order.order_date:
        try:
            created_at_override = datetime.fromisoformat(order.order_date)
        except ValueError:
            try:
                parsed_date = date.fromisoformat(order.order_date)
                created_at_override = datetime.combine(parsed_date, dtime.min)
            except ValueError:
                created_at_override = None
    with transactional_session(db):
        db_order = Order(
            from_entity_type="WAREHOUSE",
            from_entity_id=warehouse_id,
            to_entity_type="RETAILER",
            to_entity_id=order.retailer_id,
            salesman_id=salesman_id,
            created_at=created_at_override,
        )
        db.add(db_order)
        db.flush()

        for item in order.items:
            db_item = OrderItem(
                order_id=db_order.id,
                sku_id=item.sku_id,
                quantity=item.quantity,
                unit_price=round(float(item.unit_price or 0), 2),
                discount_amount=round(float(item.discount_amount or 0), 2)
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

        totals = calculate_order_totals(db_order)
        if payment_mode in {"CASH", "UPI", "CHEQUE"}:
            reference = f"{payment_mode}-{db_order.id}-{int(datetime.utcnow().timestamp())}"
            amount = totals["grand_total"]
            if payment_amount is not None:
                if payment_amount <= 0:
                    raise ValueError("Payment amount must be greater than zero")
                if payment_amount > totals["grand_total"]:
                    raise ValueError("Payment amount exceeds order total")
                amount = round(float(payment_amount), 2)
            db_payment = Account(
                order_id=db_order.id,
                amount=amount,
                transaction_reference=reference
            )
            db.add(db_payment)
            db_order.payments.append(db_payment)
            if amount >= totals["grand_total"]:
                db_order.payment_status = PaymentStatus.PAID
            else:
                db_order.payment_status = PaymentStatus.PARTIAL
    return db_order

def update_order_status(db: Session, order_id: int, status: StatusUpdate):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise ValueError("Order not found")
    next_status = status.status
    allowed_transitions = {
        OrderStatus.PENDING: {OrderStatus.OUT_FOR_DELIVERY, OrderStatus.CANCELLED},
        OrderStatus.OUT_FOR_DELIVERY: {OrderStatus.DELIVERED, OrderStatus.RETURNED, OrderStatus.CANCELLED},
        OrderStatus.DELIVERED: {OrderStatus.RETURNED},
        OrderStatus.CONFIRMED: {OrderStatus.OUT_FOR_DELIVERY, OrderStatus.CANCELLED},
        OrderStatus.RETURNED: set(),
        OrderStatus.CANCELLED: set(),
    }
    if isinstance(next_status, str):
        try:
            next_status = OrderStatus(next_status)
        except ValueError:
            raise ValueError("Invalid status transition")

    if next_status not in allowed_transitions.get(order.status, set()):
        raise ValueError("Invalid status transition")

    if status.delivery_driver_id:
        driver = db.query(Employee).filter(Employee.id == status.delivery_driver_id).first()
        if not driver or get_role_value(driver) != "DRIVER":
            raise ValueError("Invalid delivery driver")
        order.delivery_driver_id = status.delivery_driver_id

    if next_status == OrderStatus.OUT_FOR_DELIVERY:
        if not order.delivery_driver_id:
            raise ValueError("Delivery driver is required before dispatch")
        order.status = OrderStatus.OUT_FOR_DELIVERY
        if _is_outgoing_order(order):
            _allocate_inventory_for_order(db, order)
            if not order.invoice_number:
                order.invoice_number = f"INV-{order.id}"
    elif next_status == OrderStatus.CONFIRMED:
        order.status = OrderStatus.CONFIRMED
    elif next_status == OrderStatus.DELIVERED:
        order.status = OrderStatus.DELIVERED
    elif next_status in {OrderStatus.CANCELLED, OrderStatus.RETURNED}:
        order.status = next_status
        if _is_outgoing_order(order):
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
