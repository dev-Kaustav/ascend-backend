from datetime import datetime, date, timedelta
from collections import defaultdict
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models import Account, CreditNote, CreditNoteItem, InventoryTransaction, Inventory, Order, Employee, Retailer
from app.schemas.accounting import PaymentCreate, CreditNoteCreate, MultiPaymentCreate
from app.models.enums import TransactionType, PaymentStatus, OrderStatus, PaymentMode
from app.services.finance import (
    calculate_credit_note_totals,
    calculate_order_outstanding,
    calculate_order_totals,
    _round_money,
)
from app.services.transactions import transactional_session


def _parse_date(value: str | None, is_end: bool) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    if is_end:
        from datetime import timedelta
        return datetime.combine(parsed + timedelta(days=1), datetime.min.time())
    return datetime.combine(parsed, datetime.min.time())

def create_payment(db: Session, order_id: int, payment: PaymentCreate):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise ValueError("Order not found")
    existing = db.query(Account).filter(Account.transaction_reference == payment.transaction_reference).first()
    if existing:
        if existing.order_id != order_id:
            raise ValueError("Transaction reference already used for another order")
        return existing

    payment_mode_enum = None
    if payment.payment_mode:
        mode_val = payment.payment_mode.strip().upper()
        if mode_val in {m.value for m in PaymentMode}:
            payment_mode_enum = PaymentMode(mode_val)

    collection_dt = None
    if payment.collection_date:
        try:
            collection_dt = datetime.fromisoformat(payment.collection_date)
        except ValueError:
            try:
                collection_dt = datetime.combine(date.fromisoformat(payment.collection_date), datetime.min.time())
            except ValueError:
                pass

    with transactional_session(db):
        db_payment = Account(
            order_id=order_id,
            amount=_round_money(payment.amount),
            transaction_reference=payment.transaction_reference,
            payment_mode=payment_mode_enum,
            collected_by_id=payment.collected_by_id,
            collection_date=collection_dt,
            cheque_number=payment.cheque_number,
            cheque_name=payment.cheque_name,
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

def create_multi_payment(db: Session, data: MultiPaymentCreate):
    order = db.query(Order).filter(Order.id == data.order_id).first()
    if not order:
        raise ValueError("Order not found")

    collection_dt = None
    if data.collection_date:
        try:
            collection_dt = datetime.fromisoformat(data.collection_date)
        except ValueError:
            try:
                collection_dt = datetime.combine(date.fromisoformat(data.collection_date), datetime.min.time())
            except ValueError:
                pass

    created = []
    with transactional_session(db):
        for i, line in enumerate(data.payments):
            mode_val = line.payment_mode.strip().upper()
            if mode_val not in {m.value for m in PaymentMode}:
                raise ValueError(f"Invalid payment mode: {line.payment_mode}")
            payment_mode_enum = PaymentMode(mode_val)
            ref = f"{mode_val}-{order.id}-{len(order.payments) + 1}-{int(datetime.utcnow().timestamp())}-{i}"
            db_payment = Account(
                order_id=order.id,
                amount=_round_money(line.amount),
                transaction_reference=ref,
                payment_mode=payment_mode_enum,
                collected_by_id=data.collected_by_id,
                collection_date=collection_dt,
                cheque_number=line.cheque_number,
                cheque_name=line.cheque_name,
            )
            order.payments.append(db_payment)
            created.append(db_payment)

        outstanding = calculate_order_outstanding(order)
        order_total = calculate_order_totals(order)["grand_total"]
        if outstanding <= 0:
            order.payment_status = PaymentStatus.PAID
        elif outstanding < order_total:
            order.payment_status = PaymentStatus.PARTIAL
        else:
            order.payment_status = PaymentStatus.CREDIT
    return created


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
        # Reference the invoice this credit note reverses (INV-06). order.invoice is
        # read-only here — the invoice is immutable and the ORM guard would raise on any
        # write. order.invoice is None for the pre-invoice paths (direct-status test
        # fixtures, imported rows with no bill number); the reference is left NULL there.
        invoice = order.invoice
        db_credit_note = CreditNote(
            order_id=credit_note.order_id,
            credit_note_number=f"CN-{order.id}-{len(order.credit_notes) + 1}",
            invoice_id=invoice.id if invoice else None,
            original_invoice_number=invoice.invoice_number if invoice else None,
            original_invoice_date=invoice.invoice_date if invoice else None,
            note_type="C",
            note_date=datetime.utcnow(),
        )
        db.add(db_credit_note)
        db.flush()

        for item in credit_note.items:
            db_item = CreditNoteItem(
                credit_note_id=db_credit_note.id,
                sku_id=item.sku_id,
                quantity=item.quantity,
                unit_price=_round_money(item.unit_price)
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

def list_payments(db: Session, limit: int = 50, offset: int = 0, from_date: str | None = None, to_date: str | None = None):
    from_dt = _parse_date(from_date, False)
    to_dt = _parse_date(to_date, True)

    query = db.query(Account)
    if from_dt:
        query = query.filter(Account.created_at >= from_dt)
    if to_dt:
        query = query.filter(Account.created_at < to_dt)

    total = query.count()
    items = (
        query.order_by(Account.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    amount_q = db.query(func.coalesce(func.sum(Account.amount), 0))
    if from_dt:
        amount_q = amount_q.filter(Account.created_at >= from_dt)
    if to_dt:
        amount_q = amount_q.filter(Account.created_at < to_dt)
    total_amount = amount_q.scalar() or 0

    daily_since = from_dt if from_dt else (datetime.utcnow() - timedelta(days=30))
    daily_q = (
        db.query(func.date(Account.created_at), func.sum(Account.amount))
        .filter(Account.created_at >= daily_since)
    )
    if to_dt:
        daily_q = daily_q.filter(Account.created_at < to_dt)
    daily_rows = (
        daily_q
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

def list_credit_notes(db: Session, limit: int = 50, offset: int = 0, from_date: str | None = None, to_date: str | None = None):
    from_dt = _parse_date(from_date, False)
    to_dt = _parse_date(to_date, True)

    base_q = db.query(CreditNote)
    if from_dt:
        base_q = base_q.filter(CreditNote.created_at >= from_dt)
    if to_dt:
        base_q = base_q.filter(CreditNote.created_at < to_dt)

    total = base_q.count()

    amount_sub = (
        db.query(
            CreditNoteItem.credit_note_id.label("credit_note_id"),
            func.sum(CreditNoteItem.quantity * CreditNoteItem.unit_price).label("amount"),
        )
        .group_by(CreditNoteItem.credit_note_id)
        .subquery()
    )

    rows_q = (
        base_q
        .with_entities(CreditNote, amount_sub.c.amount)
        .outerjoin(amount_sub, amount_sub.c.credit_note_id == CreditNote.id)
        .order_by(CreditNote.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    items = []
    for credit_note, amount in rows_q.all():
        setattr(credit_note, "amount", round(float(amount or 0), 2))
        items.append(credit_note)

    total_q = (
        db.query(func.coalesce(func.sum(CreditNoteItem.quantity * CreditNoteItem.unit_price), 0))
        .join(CreditNote, CreditNote.id == CreditNoteItem.credit_note_id)
    )
    if from_dt:
        total_q = total_q.filter(CreditNote.created_at >= from_dt)
    if to_dt:
        total_q = total_q.filter(CreditNote.created_at < to_dt)
    total_amount = total_q.scalar() or 0
    return items, total, round(float(total_amount or 0), 2)


def get_collections(
    db: Session,
    warehouse_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    collected_by_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
):
    query = db.query(Account).join(Order, Order.id == Account.order_id)
    if warehouse_id:
        query = query.filter(Order.from_entity_id == warehouse_id)
    from_dt = _parse_date(from_date, False)
    to_dt = _parse_date(to_date, True)
    if from_dt:
        query = query.filter(Account.created_at >= from_dt)
    if to_dt:
        query = query.filter(Account.created_at < to_dt)
    if collected_by_id:
        query = query.filter(Account.collected_by_id == collected_by_id)

    total = query.count()
    items = query.order_by(Account.created_at.desc()).limit(limit).offset(offset).all()

    result = []
    for payment in items:
        order = payment.order
        retailer = db.query(Retailer).filter(Retailer.id == order.to_entity_id).first() if order else None
        collector = db.query(Employee).filter(Employee.id == payment.collected_by_id).first() if payment.collected_by_id else None
        result.append({
            "id": payment.id,
            "order_id": payment.order_id,
            "invoice_number": order.invoice_number if order else None,
            "retailer_name": retailer.name if retailer else None,
            "amount": round(payment.amount, 2),
            "payment_mode": payment.payment_mode.value if hasattr(payment.payment_mode, "value") and payment.payment_mode else None,
            "collected_by_name": collector.name if collector else None,
            "collected_by_id": payment.collected_by_id,
            "collection_date": payment.collection_date,
            "cheque_number": payment.cheque_number,
            "cheque_name": payment.cheque_name,
            "created_at": payment.created_at,
        })
    return result, total


def get_collection_summary(
    db: Session,
    warehouse_id: int | None = None,
    target_date: str | None = None,
):
    if target_date:
        try:
            d = date.fromisoformat(target_date)
        except ValueError:
            d = date.today()
    else:
        d = date.today()

    day_start = datetime.combine(d, datetime.min.time())
    day_end = datetime.combine(d + timedelta(days=1), datetime.min.time())

    query = (
        db.query(Account)
        .join(Order, Order.id == Account.order_id)
        .filter(Account.created_at >= day_start, Account.created_at < day_end)
    )
    if warehouse_id:
        query = query.filter(Order.from_entity_id == warehouse_id)

    payments = query.all()

    by_collector = defaultdict(lambda: {
        "collector_id": None,
        "collector_name": None,
        "total": 0,
        "by_mode": defaultdict(float),
        "payments": [],
    })

    total_by_mode = defaultdict(float)
    grand_total = 0
    collector_cache = {}

    for p in payments:
        cid = p.collected_by_id or 0
        if cid not in collector_cache:
            emp = db.query(Employee).filter(Employee.id == cid).first() if cid else None
            collector_cache[cid] = emp.name if emp else "Direct"

        mode = p.payment_mode.value if hasattr(p.payment_mode, "value") and p.payment_mode else "OTHER"
        c = by_collector[cid]
        c["collector_id"] = cid
        c["collector_name"] = collector_cache[cid]
        c["total"] += p.amount
        c["by_mode"][mode] += p.amount

        order = p.order
        retailer = db.query(Retailer).filter(Retailer.id == order.to_entity_id).first() if order else None
        c["payments"].append({
            "id": p.id,
            "invoice_number": order.invoice_number if order else None,
            "retailer_name": retailer.name if retailer else None,
            "amount": round(p.amount, 2),
            "payment_mode": mode,
            "time": p.created_at,
        })

        total_by_mode[mode] += p.amount
        grand_total += p.amount

    collectors = []
    for c in by_collector.values():
        c["total"] = round(c["total"], 2)
        c["by_mode"] = {k: round(v, 2) for k, v in c["by_mode"].items()}
        collectors.append(c)

    return {
        "date": d.isoformat(),
        "grand_total": round(grand_total, 2),
        "by_mode": {k: round(v, 2) for k, v in total_by_mode.items()},
        "collectors": sorted(collectors, key=lambda x: x["total"], reverse=True),
    }
