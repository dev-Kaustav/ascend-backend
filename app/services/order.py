from datetime import datetime, date, time as dtime
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models import (
    Order,
    OrderItem,
    OrderItemBatch,
    OrderItemTax,
    OrderTrail,
    Beat,
    SKUBatch,
    Inventory,
    InventoryTransaction,
    Retailer,
    Account,
    Employee,
    SKU,
    Warehouse,
    CompanyProfile,
)
from app.models.user import User
from app.schemas.order import OrderCreate, StatusUpdate
from app.models.enums import OrderStatus, TransactionType, PaymentStatus, PaymentMode
from app.services.finance import calculate_order_item_totals, calculate_order_outstanding, calculate_order_totals
from app.services.transactions import transactional_session
from app.core.deps import get_role_value


class InsufficientStockError(Exception):
    pass


class RetailerAccessError(Exception):
    pass


class StatusTransitionForbiddenError(Exception):
    pass


_STATUS_ROLE_MAP = {
    OrderStatus.READY_TO_SHIP:      {"WAREHOUSE_MANAGER"},
    OrderStatus.OUT_FOR_DELIVERY:   {"DRIVER"},
    OrderStatus.DELIVERED:          {"DRIVER"},
    OrderStatus.RETURNED:           {"WAREHOUSE_MANAGER"},
    OrderStatus.CANCELLED:          {"ADMIN", "RETAILER"},
}


def _check_role_for_transition(current_user, next_status: OrderStatus, order: "Order"):
    role = get_role_value(current_user)
    if role == "ADMIN":
        return
    if role == "RETAILER":
        if next_status != OrderStatus.CANCELLED:
            raise StatusTransitionForbiddenError("Retailer can only cancel orders")
        if not getattr(current_user, "retailer_id", None) or current_user.retailer_id != order.to_entity_id:
            raise StatusTransitionForbiddenError("Order does not belong to your account")
        return
    allowed_roles = _STATUS_ROLE_MAP.get(next_status, set())
    if role not in allowed_roles:
        raise StatusTransitionForbiddenError(
            f"Role {role} cannot set status {next_status.value}"
        )

def _is_outgoing_order(order: Order) -> bool:
    return order.from_entity_type == "WAREHOUSE" and order.to_entity_type == "RETAILER"

def _round_money(value: float) -> float:
    return round(float(value or 0), 2)

def _is_inter_state(warehouse: Warehouse | None, retailer: Retailer | None) -> bool:
    warehouse_state = getattr(warehouse, "state", None)
    retailer_state = getattr(retailer, "state", None)
    return bool(warehouse_state and retailer_state and warehouse_state != retailer_state)

def _get_company_profile_for_update(db: Session) -> CompanyProfile:
    profile = db.query(CompanyProfile).order_by(CompanyProfile.id.asc()).with_for_update().first()
    if profile:
        return profile
    profile = CompanyProfile(legal_name="Ascend Foods", invoice_prefix="ASC", invoice_next_number=1)
    db.add(profile)
    db.flush()
    return profile

def _assign_invoice_number(db: Session, order: Order):
    if order.invoice_number:
        return
    profile = _get_company_profile_for_update(db)
    prefix = (profile.invoice_prefix or "ASC").strip().upper() or "ASC"
    next_number = int(profile.invoice_next_number or 1)
    while True:
        invoice_number = f"{prefix}{next_number:06d}"
        exists = db.query(Order.id).filter(Order.invoice_number == invoice_number).first()
        if not exists:
            order.invoice_number = invoice_number
            profile.invoice_next_number = next_number + 1
            return
        next_number += 1

def _fallback_tax_rate(item) -> float:
    return sum(float(tax.rate or 0) for tax in getattr(item, "taxes", []) or [])

def _tax_rows_for_item(sku: SKU | None, item, inter_state: bool) -> list[dict]:
    sgst = float(getattr(sku, "sgst_percent", None) or 0)
    cgst = float(getattr(sku, "cgst_percent", None) or 0)
    igst = float(getattr(sku, "igst_percent", None) or 0)
    fallback = _fallback_tax_rate(item)
    if inter_state:
        rate = igst or (sgst + cgst) or fallback
        return [{"tax_type": "IGST", "rate": _round_money(rate)}] if rate > 0 else []
    if sgst or cgst:
        rows = []
        if cgst:
            rows.append({"tax_type": "CGST", "rate": _round_money(cgst)})
        if sgst:
            rows.append({"tax_type": "SGST", "rate": _round_money(sgst)})
        return rows
    fallback_same_state = fallback or igst
    if fallback_same_state:
        half = _round_money(fallback_same_state / 2)
        return [{"tax_type": "CGST", "rate": half}, {"tax_type": "SGST", "rate": _round_money(fallback_same_state - half)}]
    return []

def _reserve_inventory_for_order(db: Session, order: Order):
    warehouse_id = order.from_entity_id
    for item in order.items:
        allocated_qty = sum(batch.quantity for batch in item.order_item_batches)
        if allocated_qty >= item.quantity:
            continue
        inventory = db.query(Inventory).filter(
            Inventory.sku_id == item.sku_id,
            Inventory.warehouse_id == warehouse_id
        ).with_for_update().first()
        available_inventory = (inventory.total_quantity or 0) - (inventory.reserved_quantity or 0) if inventory else 0
        if not inventory or available_inventory < item.quantity:
            raise InsufficientStockError("Insufficient stock")

        batches = (
            db.query(SKUBatch)
            .filter(
                and_(
                    SKUBatch.sku_id == item.sku_id,
                    SKUBatch.warehouse_id == warehouse_id,
                    SKUBatch.remaining_quantity > SKUBatch.reserved_quantity,
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
            available_batch = (batch.remaining_quantity or 0) - (batch.reserved_quantity or 0)
            allocate_qty = min(remaining_qty, available_batch)
            if allocate_qty <= 0:
                continue
            order_item_batch = OrderItemBatch(
                order_item_id=item.id,
                batch_id=batch.id,
                quantity=allocate_qty
            )
            db.add(order_item_batch)
            batch.reserved_quantity = (batch.reserved_quantity or 0) + allocate_qty
            inventory.reserved_quantity = (inventory.reserved_quantity or 0) + allocate_qty
            remaining_qty -= allocate_qty
        if remaining_qty > 0:
            raise InsufficientStockError("Insufficient stock")

def _dispatch_reserved_inventory(db: Session, order: Order):
    if not _is_outgoing_order(order):
        return
    _reserve_inventory_for_order(db, order)
    warehouse_id = order.from_entity_id
    for item in order.items:
        inventory = db.query(Inventory).filter(
            Inventory.sku_id == item.sku_id,
            Inventory.warehouse_id == warehouse_id
        ).with_for_update().first()
        if not inventory:
            raise InsufficientStockError("Insufficient stock")
        for reserved in item.order_item_batches:
            batch = db.query(SKUBatch).filter(SKUBatch.id == reserved.batch_id).with_for_update().first()
            if not batch or (batch.reserved_quantity or 0) < reserved.quantity or (batch.remaining_quantity or 0) < reserved.quantity:
                raise InsufficientStockError("Insufficient stock")
            batch.reserved_quantity -= reserved.quantity
            batch.remaining_quantity -= reserved.quantity
            inventory.reserved_quantity = max((inventory.reserved_quantity or 0) - reserved.quantity, 0)
            inventory.total_quantity -= reserved.quantity
            db.add(InventoryTransaction(
                sku_id=item.sku_id,
                warehouse_id=warehouse_id,
                batch_id=batch.id,
                transaction_type=TransactionType.OUT,
                quantity=reserved.quantity
            ))

def _release_reserved_inventory(db: Session, order: Order):
    if not _is_outgoing_order(order):
        return
    warehouse_id = order.from_entity_id
    for item in order.items:
        inventory = db.query(Inventory).filter(
            Inventory.sku_id == item.sku_id,
            Inventory.warehouse_id == warehouse_id
        ).with_for_update().first()
        for reserved in list(item.order_item_batches):
            batch = db.query(SKUBatch).filter(SKUBatch.id == reserved.batch_id).with_for_update().first()
            if batch:
                batch.reserved_quantity = max((batch.reserved_quantity or 0) - reserved.quantity, 0)
            if inventory:
                inventory.reserved_quantity = max((inventory.reserved_quantity or 0) - reserved.quantity, 0)
            db.delete(reserved)

def _restore_dispatched_inventory(db: Session, order: Order):
    if not _is_outgoing_order(order):
        return
    warehouse_id = order.from_entity_id
    for item in order.items:
        for batch_link in item.order_item_batches:
            batch_link.batch.remaining_quantity += batch_link.quantity
            inventory = db.query(Inventory).filter(
                Inventory.sku_id == item.sku_id,
                Inventory.warehouse_id == warehouse_id
            ).with_for_update().first()
            if inventory:
                inventory.total_quantity += batch_link.quantity
            transaction = InventoryTransaction(
                sku_id=item.sku_id,
                warehouse_id=warehouse_id,
                batch_id=batch_link.batch_id,
                transaction_type=TransactionType.RETURN,
                quantity=batch_link.quantity
            )
            db.add(transaction)

def _record_payment(db: Session, order: Order, payment_mode: str | None, payment_amount: float | None):
    mode = (payment_mode or "").strip().upper()
    if mode == "CREDIT" or not mode:
        if not order.payments:
            order.payment_status = PaymentStatus.CREDIT
        return
    if mode not in {"CASH", "UPI", "CHEQUE"}:
        raise ValueError("Invalid payment mode")
    totals = calculate_order_totals(order)
    outstanding_before = calculate_order_outstanding(order)
    amount = outstanding_before if payment_amount is None else round(float(payment_amount), 2)
    if amount <= 0:
        raise ValueError("Payment amount must be greater than zero")
    if amount > outstanding_before:
        raise ValueError("Payment amount exceeds order total")
    payment_mode_enum = None
    if mode in {m.value for m in PaymentMode}:
        payment_mode_enum = PaymentMode(mode)
    db_payment = Account(
        order_id=order.id,
        amount=amount,
        transaction_reference=f"{mode}-{order.id}-{len(order.payments) + 1}-{int(datetime.utcnow().timestamp())}",
        payment_mode=payment_mode_enum,
    )
    db.add(db_payment)
    order.payments.append(db_payment)
    outstanding_after = calculate_order_outstanding(order)
    if outstanding_after <= 0:
        order.payment_status = PaymentStatus.PAID
    elif outstanding_after < totals["grand_total"]:
        order.payment_status = PaymentStatus.PARTIAL
    else:
        order.payment_status = PaymentStatus.CREDIT

def _build_trail_description(order: Order, next_status: OrderStatus) -> str:
    if next_status == OrderStatus.READY_TO_SHIP:
        return "Marked ready to ship"
    if next_status == OrderStatus.OUT_FOR_DELIVERY:
        return "Out for delivery"
    if next_status == OrderStatus.DELIVERED:
        return "Delivered"
    if next_status == OrderStatus.RETURNED:
        return "Returned"
    if next_status == OrderStatus.CANCELLED:
        return "Cancelled"
    return f"Status changed to {next_status.value}"


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
    warehouse = db.query(Warehouse).filter(Warehouse.id == warehouse_id).first()
    retailer = db.query(Retailer).filter(Retailer.id == order.retailer_id).first()
    inter_state = _is_inter_state(warehouse, retailer)
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
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.CREDIT,
            created_at=created_at_override,
        )
        db.add(db_order)
        db.flush()
        _assign_invoice_number(db, db_order)

        for item in order.items:
            sku = db.query(SKU).filter(SKU.id == item.sku_id).first()
            db_item = OrderItem(
                order_id=db_order.id,
                sku_id=item.sku_id,
                quantity=item.quantity,
                unit_price=round(float(item.unit_price or 0), 2),
                discount_amount=round(float(item.discount_amount or 0), 2)
            )
            db.add(db_item)
            db.flush()

            for tax in _tax_rows_for_item(sku, item, inter_state):
                db_tax = OrderItemTax(
                    order_item_id=db_item.id,
                    tax_type=tax["tax_type"],
                    rate=tax["rate"]
                )
                db.add(db_tax)

        db.add(OrderTrail(
            order_id=db_order.id,
            order_status=OrderStatus.PENDING,
            description="Order created",
            changed_by_id=getattr(current_user, "id", None),
        ))

        if _is_outgoing_order(db_order):
            _reserve_inventory_for_order(db, db_order)
        _record_payment(db, db_order, payment_mode, payment_amount)
    return db_order

def update_order_status(db: Session, order_id: int, status: StatusUpdate, current_user=None):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise ValueError("Order not found")
    next_status = status.status
    allowed_transitions = {
        OrderStatus.PENDING: {OrderStatus.READY_TO_SHIP, OrderStatus.CANCELLED},
        OrderStatus.READY_TO_SHIP: {OrderStatus.OUT_FOR_DELIVERY, OrderStatus.CANCELLED},
        OrderStatus.OUT_FOR_DELIVERY: {OrderStatus.DELIVERED, OrderStatus.RETURNED, OrderStatus.CANCELLED},
        OrderStatus.DELIVERED: {OrderStatus.RETURNED},
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

    if current_user is not None:
        _check_role_for_transition(current_user, next_status, order)

    if status.delivery_driver_id:
        driver = db.query(Employee).filter(Employee.id == status.delivery_driver_id).first()
        if not driver or get_role_value(driver) != "DRIVER":
            raise ValueError("Invalid delivery driver")
        order.delivery_driver_id = status.delivery_driver_id

    if status.delivery_date:
        try:
            order.delivery_date = datetime.fromisoformat(status.delivery_date)
        except ValueError:
            try:
                order.delivery_date = datetime.combine(date.fromisoformat(status.delivery_date), dtime.min)
            except ValueError:
                pass
    if status.panel_status is not None:
        order.panel_status = status.panel_status
    if status.issue_category is not None:
        from app.models.enums import IssueCategory
        try:
            order.issue_category = IssueCategory(status.issue_category)
        except ValueError:
            pass
    if status.description is not None:
        order.description = status.description

    previous_status = order.status

    if next_status == OrderStatus.READY_TO_SHIP:
        if not order.delivery_driver_id:
            raise ValueError("Delivery driver is required before marking ready to ship")
        order.status = OrderStatus.READY_TO_SHIP
    elif next_status == OrderStatus.OUT_FOR_DELIVERY:
        order.status = OrderStatus.OUT_FOR_DELIVERY
        _dispatch_reserved_inventory(db, order)
    elif next_status == OrderStatus.DELIVERED:
        order.status = OrderStatus.DELIVERED
        if status.payment_status:
            try:
                requested_payment_status = PaymentStatus(status.payment_status)
            except ValueError:
                raise ValueError("Invalid payment status")
            if requested_payment_status == PaymentStatus.CREDIT:
                order.payment_status = PaymentStatus.CREDIT if not order.payments else order.payment_status
            elif requested_payment_status in {PaymentStatus.PAID, PaymentStatus.PARTIAL}:
                _record_payment(db, order, status.payment_mode or "CASH", status.payment_amount)
        elif status.payment_amount is not None:
            _record_payment(db, order, status.payment_mode or "CASH", status.payment_amount)
    elif next_status in {OrderStatus.CANCELLED, OrderStatus.RETURNED}:
        order.status = next_status
        if previous_status in {OrderStatus.PENDING, OrderStatus.READY_TO_SHIP}:
            _release_reserved_inventory(db, order)
        else:
            _restore_dispatched_inventory(db, order)

    trail_description = status.description or _build_trail_description(order, next_status)
    db.add(OrderTrail(
        order_id=order.id,
        order_status=next_status,
        description=trail_description,
        changed_by_id=getattr(current_user, "id", None) if current_user else None,
    ))

    db.commit()
    return order

def _serialize_order(db: Session, order: Order) -> dict:
    warehouse = db.query(Warehouse).filter(Warehouse.id == order.from_entity_id).first()
    retailer = db.query(Retailer).filter(Retailer.id == order.to_entity_id).first()
    beat = db.query(Beat).filter(Beat.id == order.beat_id).first() if order.beat_id else None
    salesman = db.query(Employee).filter(Employee.id == order.salesman_id).first() if order.salesman_id else None
    driver = db.query(Employee).filter(Employee.id == order.delivery_driver_id).first() if order.delivery_driver_id else None
    sku_ids = [item.sku_id for item in order.items]
    sku_map = {sku.id: sku for sku in db.query(SKU).filter(SKU.id.in_(sku_ids)).all()} if sku_ids else {}
    totals = calculate_order_totals(order)
    items = []
    for item in order.items:
        sku = sku_map.get(item.sku_id)
        item_totals = calculate_order_item_totals(item)
        items.append({
            "id": item.id,
            "sku_id": item.sku_id,
            "sku_name": sku.name if sku else None,
            "hsn_code": sku.hsn_code if sku else None,
            "quantity": _round_money(item.quantity),
            "unit_price": _round_money(item.unit_price),
            "discount_amount": _round_money(item.discount_amount),
            "taxable_value": item_totals["taxable_value"],
            "gst_amount": item_totals["gst_amount"],
            "line_total": item_totals["line_total"],
            "taxes": [
                {
                    "id": tax.id,
                    "tax_type": tax.tax_type,
                    "rate": _round_money(tax.rate),
                }
                for tax in item.taxes
            ],
        })
    payments = [
        {
            "id": payment.id,
            "amount": _round_money(payment.amount),
            "transaction_reference": payment.transaction_reference,
            "payment_mode": payment.payment_mode.value if hasattr(payment.payment_mode, "value") else payment.payment_mode,
            "created_at": payment.created_at,
        }
        for payment in order.payments
    ]
    credit_notes = [
        {
            "id": note.id,
            "credit_note_number": note.credit_note_number,
            "applies_to_outstanding": note.applies_to_outstanding,
            "created_at": note.created_at,
            "items": [
                {
                    "id": item.id,
                    "sku_id": item.sku_id,
                    "quantity": _round_money(item.quantity),
                    "unit_price": _round_money(item.unit_price),
                }
                for item in note.items
            ],
        }
        for note in order.credit_notes
    ]
    trail_user_ids = {t.changed_by_id for t in order.trails if t.changed_by_id}
    trail_user_map = {}
    if trail_user_ids:
        users = db.query(User).filter(User.id.in_(trail_user_ids)).all()
        for u in users:
            emp = db.query(Employee).filter(Employee.id == u.employee_id).first() if u.employee_id else None
            trail_user_map[u.id] = emp.name if emp else u.email
    trails = [
        {
            "id": t.id,
            "order_status": t.order_status.value if hasattr(t.order_status, "value") else t.order_status,
            "description": t.description,
            "changed_by_name": trail_user_map.get(t.changed_by_id),
            "created_at": t.created_at,
        }
        for t in order.trails
    ]
    return {
        "id": order.id,
        "from_entity_type": order.from_entity_type,
        "from_entity_id": order.from_entity_id,
        "to_entity_type": order.to_entity_type,
        "to_entity_id": order.to_entity_id,
        "status": order.status.value if hasattr(order.status, "value") else order.status,
        "invoice_number": order.invoice_number,
        "beat_id": order.beat_id,
        "beat_name": beat.name if beat else None,
        "salesman_id": order.salesman_id,
        "delivery_driver_id": order.delivery_driver_id,
        "delivery_date": order.delivery_date,
        "panel_status": order.panel_status,
        "issue_category": order.issue_category.value if hasattr(order.issue_category, "value") and order.issue_category else order.issue_category,
        "description": order.description,
        "payment_status": order.payment_status.value if hasattr(order.payment_status, "value") else order.payment_status,
        "items": items,
        "warehouse_name": warehouse.name if warehouse else None,
        "warehouse_state": warehouse.state if warehouse else None,
        "retailer_name": retailer.name if retailer else None,
        "retailer_address_line1": retailer.address_line1 if retailer else None,
        "retailer_city": retailer.city if retailer else None,
        "retailer_state": retailer.state if retailer else None,
        "retailer_pincode": retailer.pincode if retailer else None,
        "retailer_gst_number": retailer.gst_number if retailer else None,
        "salesman_name": salesman.name if salesman else None,
        "salesman_phone": salesman.phone_number if salesman else None,
        "delivery_driver_name": driver.name if driver else None,
        "total_amount": totals["grand_total"],
        "pending_amount": calculate_order_outstanding(order),
        "taxable_value": totals["taxable_value"],
        "gst_amount": totals["gst_amount"],
        "subtotal": totals["subtotal"],
        "grand_total": totals["grand_total"],
        "payments": payments,
        "credit_notes": credit_notes,
        "trails": trails,
        "created_at": order.created_at,
    }

def get_order_detail(db: Session, order_id: int):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise ValueError("Order not found")
    return _serialize_order(db, order)

def get_order_invoice_view(db: Session, order_id: int):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise ValueError("Order not found")
    totals = calculate_order_totals(order)
    return {"order": _serialize_order(db, order), **totals}
