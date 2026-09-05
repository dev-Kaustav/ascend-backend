from datetime import datetime, date, time as dtime
from decimal import Decimal
from sqlalchemy.orm import Session, selectinload
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
    CreditNote,
    Employee,
    SKU,
    Warehouse,
)
from app.models.user import User
from app.schemas.order import OrderCreate, StatusUpdate
from app.models.enums import OrderStatus, TransactionType, PaymentStatus, PaymentMode
from app.services.finance import (
    calculate_order_item_totals,
    calculate_order_outstanding,
    calculate_order_totals,
    _round_money,
)
from app.services.invoice import issue_invoice_for_order
from app.services.transactions import transactional_session
from app.core.deps import get_role_value
# Module-qualified import (not a from-import): current_business_date is looked
# up at call time as inventory_service.current_business_date(), which is what
# lets tests monkeypatch the seam (D1).
from app.services import inventory as inventory_service


class InsufficientStockError(Exception):
    pass


class RetailerAccessError(Exception):
    pass


class StatusTransitionForbiddenError(Exception):
    pass


class OrderScopeError(Exception):
    pass


class OrderNotFoundError(ValueError):
    pass


# Single authority for both legality (is this (from, to) pair reachable at all?) and role
# (which non-admin roles may perform it?). A (from, to) pair absent from this table is an
# illegal transition; the mapped frozenset is the set of non-admin roles permitted to make
# it. RETURNED is reserved for a future partial-cancellation feature (D1) and deliberately
# appears on neither side of any row here.
_ALLOWED_TRANSITIONS: dict[tuple[OrderStatus, OrderStatus], frozenset[str]] = {
    (OrderStatus.PENDING, OrderStatus.READY_TO_SHIP): frozenset({"WAREHOUSE_MANAGER"}),
    (OrderStatus.READY_TO_SHIP, OrderStatus.OUT_FOR_DELIVERY): frozenset({"DRIVER"}),
    (OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED): frozenset({"DRIVER"}),
    (OrderStatus.PENDING, OrderStatus.CANCELLED): frozenset({"ADMIN", "RETAILER"}),
    # Retailer included per amended D2 (2026-08-16): a retailer may cancel until the goods
    # leave — READY_TO_SHIP is picked/assigned but not yet dispatched, so this is still
    # self-serve. Matches components/orders/RetailerOrders.jsx's existing CANCELLABLE set.
    (OrderStatus.READY_TO_SHIP, OrderStatus.CANCELLED): frozenset({"ADMIN", "WAREHOUSE_MANAGER", "RETAILER"}),
    (OrderStatus.OUT_FOR_DELIVERY, OrderStatus.CANCELLED): frozenset({"DRIVER", "ADMIN"}),
    (OrderStatus.DELIVERED, OrderStatus.CANCELLED): frozenset({"ADMIN"}),
}


def allowed_next_statuses(current: OrderStatus) -> frozenset[OrderStatus]:
    return frozenset(to for (frm, to) in _ALLOWED_TRANSITIONS if frm == current)


def roles_for_transition(current: OrderStatus, next_status: OrderStatus) -> frozenset[str]:
    return _ALLOWED_TRANSITIONS.get((current, next_status), frozenset())


def _check_role_for_transition(current_user, next_status: OrderStatus, order: "Order"):
    role = get_role_value(current_user)
    allowed = roles_for_transition(order.status, next_status)
    # Blanket admin override, preserved pre-existing behaviour (not a D2 row). The table
    # enumerates role-specific grants; it does not withdraw admin's ability to advance an
    # order. See 03-CONTEXT.md judgement call 1.
    if role == "ADMIN":
        return
    if role == "RETAILER":
        if not getattr(current_user, "retailer_id", None) or current_user.retailer_id != order.to_entity_id:
            raise StatusTransitionForbiddenError("Order does not belong to your account")
        # Fall through to the table check below — a retailer is only listed on
        # (PENDING, CANCELLED) and (READY_TO_SHIP, CANCELLED); any other transition is
        # denied by the membership check.
    if role == "DRIVER":
        if (
            not getattr(current_user, "employee_id", None)
            or current_user.employee_id != order.delivery_driver_id
        ):
            raise StatusTransitionForbiddenError("Order is not assigned to this driver")
    if role not in allowed:
        raise StatusTransitionForbiddenError(
            f"Role {role} cannot move order from {order.status.value} to {next_status.value}"
        )

def _is_outgoing_order(order: Order) -> bool:
    return order.from_entity_type == "WAREHOUSE" and order.to_entity_type == "RETAILER"


def _assert_order_in_scope(current_user, order: Order, db: Session):
    role = get_role_value(current_user)
    if role in {"ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER"}:
        return
    if role == "SALESMAN":
        employee_id = getattr(current_user, "employee_id", None)
        if not employee_id:
            raise OrderScopeError("Salesman missing employee record")
        retailer = db.query(Retailer).filter(Retailer.id == order.to_entity_id).first()
        if not retailer or retailer.assigned_salesman_id != employee_id:
            raise OrderScopeError("Order retailer is not assigned to this salesman")
        return
    if role == "DRIVER":
        employee_id = getattr(current_user, "employee_id", None)
        if not employee_id or order.delivery_driver_id != employee_id:
            raise OrderScopeError("Order is not assigned to this driver")
        return
    if role == "RETAILER":
        retailer_id = getattr(current_user, "retailer_id", None)
        if not retailer_id or order.to_entity_id != retailer_id:
            raise OrderScopeError("Order does not belong to your account")
        return
    raise OrderScopeError("Order access is not permitted for this role")


def scoped_orders_query(db: Session, current_user):
    query = db.query(Order).filter(
        Order.from_entity_type == "WAREHOUSE",
        Order.to_entity_type == "RETAILER",
    )
    role = get_role_value(current_user)
    if role in {"ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER"}:
        return query
    if role == "SALESMAN":
        employee_id = getattr(current_user, "employee_id", None)
        if not employee_id:
            raise OrderScopeError("Salesman missing employee record")
        return query.join(Retailer, Retailer.id == Order.to_entity_id).filter(
            Retailer.assigned_salesman_id == employee_id
        )
    if role == "DRIVER":
        employee_id = getattr(current_user, "employee_id", None)
        if not employee_id:
            raise OrderScopeError("Driver missing employee record")
        return query.filter(Order.delivery_driver_id == employee_id)
    if role == "RETAILER":
        retailer_id = getattr(current_user, "retailer_id", None)
        if not retailer_id:
            raise OrderScopeError("Retailer missing retailer record")
        return query.filter(Order.to_entity_id == retailer_id)
    raise OrderScopeError("Order access is not permitted for this role")

def order_form_lookups(db: Session, current_user):
    """Lookups for the order-creation form, scoped the same way create_outgoing_order
    scopes its write. A salesman sees only the retailers assigned to them, so the form
    cannot offer a retailer the POST would reject with RetailerAccessError.

    Deliberately narrower than /admin/lookups: no salesmen, warehouse managers, drivers
    or beats. Those are admin-console lookups; an order form needs retailers, SKUs,
    warehouses and brands, and nothing on this endpoint should widen a salesman's read
    surface beyond what they already have.
    """
    from app.services.admin import list_brands, list_retailers, list_skus, list_warehouses

    role = get_role_value(current_user)
    if role in {"ADMIN", "ACCOUNTANT"}:
        retailers = list_retailers(db)
    elif role == "SALESMAN":
        employee_id = getattr(current_user, "employee_id", None)
        if not employee_id:
            raise OrderScopeError("Salesman missing employee record")
        retailers = (
            db.query(Retailer)
            .filter(Retailer.assigned_salesman_id == employee_id)
            .order_by(Retailer.name.asc())
            .all()
        )
    else:
        raise OrderScopeError("Order creation is not permitted for this role")

    return {
        "retailers": retailers,
        "skus": list_skus(db),
        "warehouses": list_warehouses(db),
        "brands": list_brands(db),
    }


def _is_inter_state(warehouse: Warehouse | None, retailer: Retailer | None) -> bool:
    warehouse_state = getattr(warehouse, "state", None)
    retailer_state = getattr(retailer, "state", None)
    return bool(warehouse_state and retailer_state and warehouse_state != retailer_state)

def _fallback_tax_rate(item) -> Decimal:
    # tax.rate arrives as a float here: the caller passes the request item, whose taxes are
    # OrderItemTaxCreate (rate: float), not the persisted OrderItemTax (rate: Numeric). Summing
    # those onto a Decimal start value raises TypeError, which no caller catches, so every order
    # carrying a tax line 500s. Normalise through str() so 2.5 stays 2.5 rather than picking up
    # the binary-float tail Decimal(2.5) would.
    return sum(
        (Decimal(str(tax.rate)) for tax in getattr(item, "taxes", []) or [] if tax.rate),
        Decimal("0"),
    )

def _tax_rows_for_item(sku: SKU | None, item, inter_state: bool) -> list[dict]:
    sgst = getattr(sku, "sgst_percent", None) or Decimal("0")
    cgst = getattr(sku, "cgst_percent", None) or Decimal("0")
    igst = getattr(sku, "igst_percent", None) or Decimal("0")
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
    # Resolved once per call, not once per batch, so a call that straddles
    # midnight cannot allocate against two different definitions of "today"
    # within a single order (D1).
    as_of = inventory_service.current_business_date()
    for item in order.items:
        allocated_qty = sum(batch.quantity for batch in item.order_item_batches)
        if allocated_qty >= item.quantity:
            continue
        inventory = db.query(Inventory).filter(
            Inventory.sku_id == item.sku_id,
            Inventory.warehouse_id == warehouse_id
        ).with_for_update().first()
        available_inventory = (inventory.total_quantity or 0) - (inventory.reserved_quantity or 0) if inventory else 0
        # This aggregate pre-check counts expired units, so an order can pass
        # here and still run out of allocatable batches below. That's fine:
        # the batch loop's "remaining_qty > 0" check is the authority on
        # availability, and the caller sees the same InsufficientStockError /
        # 409 either way (D5 — tightening this would cost a second query per
        # item for no behavioural difference).
        if not inventory or available_inventory < item.quantity:
            raise InsufficientStockError("Insufficient stock")

        batches = (
            db.query(SKUBatch)
            .filter(
                and_(
                    SKUBatch.sku_id == item.sku_id,
                    SKUBatch.warehouse_id == warehouse_id,
                    SKUBatch.remaining_quantity > SKUBatch.reserved_quantity,
                    inventory_service.allocatable_batch_criterion(as_of),
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
    # Resolved once per call (same reasoning as _reserve_inventory_for_order):
    # a batch reserved earlier may have expired since reservation, and this is
    # the last check before physical stock leaves the building (D1).
    as_of = inventory_service.current_business_date()
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
            if batch.expiry_date is not None and batch.expiry_date < as_of:
                # Distinct message (R1): the batch was fine when reserved but
                # has since expired. "Insufficient stock" would send the
                # operator hunting a phantom stock problem when the warehouse
                # may be full. No automatic re-allocation path (D5) — the
                # order must be cancelled and re-placed.
                raise InsufficientStockError(
                    "Expired stock cannot be dispatched. Cancel and re-place this order to "
                    "reserve fresh, non-expired stock."
                )
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
            # Locked, explicit query — same shape _release_reserved_inventory already uses
            # (:241) — instead of the lazy `batch_link.batch` relationship. Two things fixed
            # by this one change:
            # - The lock. Every other read-modify-write on sku_batches in this module takes
            #   with_for_update() first (reserve, dispatch, release). Reading through the
            #   relationship and writing back an absolute value derived from that read meant
            #   a concurrent writer committing between the two was silently overwritten —
            #   this was the only unserialised one of the four.
            # - The guard. batch_id is NOT NULL with a foreign key and no ON DELETE, so the
            #   row cannot actually vanish — but the sibling function guards its read and
            #   this one did not, which made the two impossible to tell apart by inspection.
            #   Now both read the same way.
            # populate_existing() is required, not decorative: if this session already holds
            # this SKUBatch in its identity map (e.g. something upstream walked
            # batch_link.batch before reaching cancel), a bare with_for_update() still takes
            # the row lock at the SQL level but SQLAlchemy will *not* overwrite the
            # already-loaded Python attributes with the freshly locked row's values — it
            # silently keeps serving the stale in-memory copy. That is precisely the
            # instance-vs-current-row gap this task closes, and it is the same pattern
            # update_order_status already uses at :398 (populate_existing().with_for_update()).
            batch = (
                db.query(SKUBatch)
                .filter(SKUBatch.id == batch_link.batch_id)
                .populate_existing()
                .with_for_update()
                .first()
            )
            if batch:
                batch.remaining_quantity += batch_link.quantity
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
    amount = outstanding_before if payment_amount is None else _round_money(payment_amount)
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

    with transactional_session(db):
        db_order = Order(
            from_entity_type="WAREHOUSE",
            from_entity_id=warehouse_id,
            to_entity_type="RETAILER",
            to_entity_id=order.retailer_id,
            salesman_id=salesman_id,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.CREDIT,
        )
        db.add(db_order)
        db.flush()

        for item in order.items:
            sku = db.query(SKU).filter(SKU.id == item.sku_id).first()
            db_item = OrderItem(
                order_id=db_order.id,
                sku_id=item.sku_id,
                quantity=item.quantity,
                unit_price=_round_money(item.unit_price),
                discount_amount=_round_money(item.discount_amount)
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

def update_order_status(db: Session, order_id: int, status: StatusUpdate, current_user):
    # ORD-03: lock this row before reading order.status. Two concurrent callers must not
    # both pass the legality check below against the same stale status — the loser has to
    # block here, then see the winner's committed status once unblocked.
    order = db.query(Order).filter(Order.id == order_id).populate_existing().with_for_update().first()
    if not order:
        raise OrderNotFoundError("Order not found")
    _assert_order_in_scope(current_user, order, db)
    next_status = status.status
    if isinstance(next_status, str):
        try:
            next_status = OrderStatus(next_status)
        except ValueError:
            raise ValueError("Invalid status transition")

    if next_status not in allowed_next_statuses(order.status):
        raise ValueError("Invalid status transition")

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
        # Issue the tax invoice only after dispatch succeeds — a short-picked batch raises
        # InsufficientStockError above and must not burn an invoice number (D-01). nextval()
        # is not transactional, so a failure later in this same request still leaves a gap
        # in the sequence; D-04 accepts gaps and only rejects collisions.
        issue_invoice_for_order(db, order)
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
    elif next_status == OrderStatus.CANCELLED:
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
        changed_by_id=getattr(current_user, "id", None),
    ))

    db.commit()
    return order

def _order_detail_query(db: Session):
    """Eager-load the relationship graph one order's serialization walks, in a fixed
    number of statements regardless of how many items/payments/credit notes/trails the
    order has (ORD-04). Uses selectin-style batched loading — these are all
    one-to-many collections, and a join-based eager load would fan the result set out
    into a cartesian product across four collections."""
    return db.query(Order).options(
        selectinload(Order.items).selectinload(OrderItem.taxes),
        selectinload(Order.payments),
        selectinload(Order.credit_notes).selectinload(CreditNote.items),
        selectinload(Order.trails),
    )

def _serialize_order(db: Session, order: Order) -> dict:
    warehouse = db.query(Warehouse).filter(Warehouse.id == order.from_entity_id).first()
    retailer = db.query(Retailer).filter(Retailer.id == order.to_entity_id).first()
    beat = db.query(Beat).filter(Beat.id == order.beat_id).first() if order.beat_id else None
    employee_ids = {eid for eid in (order.salesman_id, order.delivery_driver_id) if eid}
    employee_map = {}
    if employee_ids:
        employee_map = {e.id: e for e in db.query(Employee).filter(Employee.id.in_(employee_ids)).all()}
    salesman = employee_map.get(order.salesman_id) if order.salesman_id else None
    driver = employee_map.get(order.delivery_driver_id) if order.delivery_driver_id else None
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
            "sku_code": sku.code if sku else None,
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
            "invoice_id": note.invoice_id,
            "original_invoice_number": note.original_invoice_number,
            "original_invoice_date": note.original_invoice_date,
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
        # outerjoin, not join: a user with no employee_id, or one pointing at a deleted
        # row, must still resolve to their email — an inner join would silently drop
        # those authors and their trail entries would render changed_by_name: null.
        rows = (
            db.query(User.id, User.email, Employee.name)
            .outerjoin(Employee, Employee.id == User.employee_id)
            .filter(User.id.in_(trail_user_ids))
            .all()
        )
        for user_id, email, employee_name in rows:
            trail_user_map[user_id] = employee_name or email
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

def get_order_detail(db: Session, order_id: int, current_user):
    order = _order_detail_query(db).filter(Order.id == order_id).first()
    if not order:
        raise OrderNotFoundError("Order not found")
    _assert_order_in_scope(current_user, order, db)
    return _serialize_order(db, order)

def get_order_invoice_view(db: Session, order_id: int, current_user):
    order = _order_detail_query(db).filter(Order.id == order_id).first()
    if not order:
        raise OrderNotFoundError("Order not found")
    _assert_order_in_scope(current_user, order, db)
    totals = calculate_order_totals(order)
    return {"order": _serialize_order(db, order), **totals}


def get_order_for_invoice_pdf(db: Session, order_id: int, current_user):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise OrderNotFoundError("Order not found")
    _assert_order_in_scope(current_user, order, db)
    return order
