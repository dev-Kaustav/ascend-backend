from datetime import datetime, timedelta
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Account, CreditNote, CreditNoteItem, InventoryTransaction, Inventory, Order
from app.schemas.accounting import PaymentCreate, CreditNoteCreate
from app.models.enums import TransactionType, PaymentStatus, OrderStatus
from app.services.finance import calculate_credit_note_totals, calculate_order_outstanding, calculate_order_totals
from app.services.transactions import transactional_session

def create_payment(db: Session, order_id: int, payment: PaymentCreate):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise ValueError("Order not found")
    existing = db.query(Account).filter(Account.transaction_reference == payment.transaction_reference).first()
    if existing:
        if existing.order_id != order_id:
            raise ValueError("Transaction reference already used for another order")
        return existing

    with transactional_session(db):
        db_payment = Account(
            order_id=order_id,
            amount=round(float(payment.amount or 0), 2),
            transaction_reference=payment.transaction_reference
        )
        order.payments.append(db_payment)

        outstanding = calculate_order_outstanding(order)
        order_total = calculate_order_totals(order)["grand_total"]
        if outstanding <= 0:
            order.payment_status = PaymentStatus.PAID
        elif outstanding < order_total:
            order.payment_status = PaymentStatus.PARTIAL
        else:
            order.payment_status = PaymentStatus.CREDIT
    return db_payment

def create_credit_note(db: Session, credit_note: CreditNoteCreate):
    order = db.query(Order).filter(Order.id == credit_note.order_id).first()
    if not order or order.status != OrderStatus.DELIVERED:
        raise ValueError("Order not delivered")
    for item in credit_note.items:
        original_qty = sum(oi.quantity for oi in order.items if oi.sku_id == item.sku_id)
        credited_qty = sum(
            cni.quantity
            for cn in order.credit_notes
            for cni in cn.items
            if cni.sku_id == item.sku_id
        )
        if credited_qty + item.quantity > original_qty:
            raise ValueError("Credit quantity exceeds original")

    with transactional_session(db):
        db_credit_note = CreditNote(
            order_id=credit_note.order_id,
            credit_note_number=f"CN-{order.id}-{len(order.credit_notes) + 1}"
        )
        db.add(db_credit_note)
        db.flush()

        for item in credit_note.items:
            db_item = CreditNoteItem(
                credit_note_id=db_credit_note.id,
                sku_id=item.sku_id,
                quantity=item.quantity,
                unit_price=round(float(item.unit_price or 0), 2)
            )
            db.add(db_item)
            if credit_note.restock:
                inventory = db.query(Inventory).filter(
                    Inventory.sku_id == item.sku_id,
                    Inventory.warehouse_id == order.from_entity_id
                ).with_for_update().first()
                if not inventory:
                    inventory = Inventory(
                        sku_id=item.sku_id,
                        warehouse_id=order.from_entity_id,
                        total_quantity=0
                    )
                    db.add(inventory)
                inventory.total_quantity += item.quantity
                transaction = InventoryTransaction(
                    sku_id=item.sku_id,
                    warehouse_id=order.from_entity_id,
                    transaction_type=TransactionType.RETURN,
                    quantity=item.quantity
                )
                db.add(transaction)

        db.flush()
        outstanding = calculate_order_outstanding(order)
        order_total = calculate_order_totals(order)["grand_total"]
        if outstanding <= 0:
            order.payment_status = PaymentStatus.PAID
        elif outstanding < order_total:
            order.payment_status = PaymentStatus.PARTIAL
        else:
            order.payment_status = PaymentStatus.CREDIT
    return db_credit_note

def get_credit_note_view(db: Session, credit_note_id: int):
    credit_note = db.query(CreditNote).filter(CreditNote.id == credit_note_id).first()
    if not credit_note:
        raise ValueError("Credit note not found")
    totals = calculate_credit_note_totals(credit_note)
    return {"credit_note": credit_note, **totals}

def list_payments(db: Session, limit: int = 50, offset: int = 0):
    query = db.query(Account)
    total = query.count()
    items = (
        query.order_by(Account.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    total_amount = db.query(func.coalesce(func.sum(Account.amount), 0)).scalar() or 0

    since = datetime.utcnow() - timedelta(days=30)
    daily_rows = (
        db.query(func.date(Account.created_at), func.sum(Account.amount))
        .filter(Account.created_at >= since)
        .group_by(func.date(Account.created_at))
        .order_by(func.date(Account.created_at))
        .all()
    )
    daily_totals = [
        {"date": day.isoformat(), "amount": round(float(amount or 0), 2)}
        for day, amount in daily_rows
        if day
    ]
    return items, total, round(float(total_amount or 0), 2), daily_totals

def list_credit_notes(db: Session, limit: int = 50, offset: int = 0):
    total = db.query(func.count(CreditNote.id)).scalar() or 0

    amount_sub = (
        db.query(
            CreditNoteItem.credit_note_id.label("credit_note_id"),
            func.sum(CreditNoteItem.quantity * CreditNoteItem.unit_price).label("amount"),
        )
        .group_by(CreditNoteItem.credit_note_id)
        .subquery()
    )

    rows = (
        db.query(CreditNote, amount_sub.c.amount)
        .outerjoin(amount_sub, amount_sub.c.credit_note_id == CreditNote.id)
        .order_by(CreditNote.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    items = []
    for credit_note, amount in rows:
        setattr(credit_note, "amount", round(float(amount or 0), 2))
        items.append(credit_note)

    total_amount = (
        db.query(func.coalesce(func.sum(CreditNoteItem.quantity * CreditNoteItem.unit_price), 0))
        .scalar()
        or 0
    )
    return items, total, round(float(total_amount or 0), 2)
