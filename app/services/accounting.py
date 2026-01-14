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
            amount=payment.amount,
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
            order.payment_status = PaymentStatus.UNPAID
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
                unit_price=item.unit_price
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
            order.payment_status = PaymentStatus.UNPAID
    return db_credit_note

def get_credit_note_view(db: Session, credit_note_id: int):
    credit_note = db.query(CreditNote).filter(CreditNote.id == credit_note_id).first()
    if not credit_note:
        raise ValueError("Credit note not found")
    totals = calculate_credit_note_totals(credit_note)
    return {"credit_note": credit_note, **totals}

def list_payments(db: Session):
    return db.query(Account).order_by(Account.created_at.desc()).all()

def list_credit_notes(db: Session):
    return db.query(CreditNote).order_by(CreditNote.created_at.desc()).all()
