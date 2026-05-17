from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services.accounting import create_payment, create_multi_payment, create_credit_note, get_credit_note_view, list_payments, list_credit_notes, get_collections, get_collection_summary
from app.services.outstanding import get_outstanding_orders, get_outstanding_summary
from app.schemas.accounting import (
    PaymentCreate,
    MultiPaymentCreate,
    PaymentResponse,
    PaymentPage,
    CreditNoteCreate,
    CreditNoteResponse,
    CreditNotePage,
    CreditNoteView,
)
from app.core.deps import require_accountant

router = APIRouter()
public_router = APIRouter()

@router.post("/orders/{order_id}/payments", response_model=PaymentResponse)
def create_payment_endpoint(order_id: int, payment: PaymentCreate, db: Session = Depends(get_db), current_user = Depends(require_accountant)):
    try:
        return create_payment(db, order_id, payment)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/payments/multi", response_model=list[PaymentResponse])
def create_multi_payment_endpoint(data: MultiPaymentCreate, db: Session = Depends(get_db), current_user = Depends(require_accountant)):
    try:
        return create_multi_payment(db, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/credit-notes", response_model=CreditNoteResponse)
def create_credit_note_endpoint(credit_note: CreditNoteCreate, db: Session = Depends(get_db), current_user = Depends(require_accountant)):
    try:
        return create_credit_note(db, credit_note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/payments", response_model=PaymentPage)
def list_payments_endpoint(
    db: Session = Depends(get_db),
    current_user = Depends(require_accountant),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
):
    items, total, total_amount, daily_totals = list_payments(db, limit=limit, offset=offset, from_date=from_date, to_date=to_date)
    return {
        "items": items,
        "total": total,
        "total_amount": total_amount,
        "daily_totals": daily_totals,
    }

@router.get("/credit-notes", response_model=CreditNotePage)
def list_credit_notes_endpoint(
    db: Session = Depends(get_db),
    current_user = Depends(require_accountant),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
):
    items, total, total_amount = list_credit_notes(db, limit=limit, offset=offset, from_date=from_date, to_date=to_date)
    return {"items": items, "total": total, "total_amount": total_amount}

@router.get("/outstanding")
def outstanding_orders_endpoint(
    db: Session = Depends(get_db),
    current_user=Depends(require_accountant),
    warehouse_id: int | None = Query(None),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
    salesman_id: int | None = Query(None),
    retailer_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    items, total = get_outstanding_orders(
        db, warehouse_id=warehouse_id, from_date=from_date, to_date=to_date,
        salesman_id=salesman_id, retailer_id=retailer_id, limit=limit, offset=offset,
    )
    return {"items": items, "total": total}

@router.get("/outstanding/summary")
def outstanding_summary_endpoint(
    db: Session = Depends(get_db),
    current_user=Depends(require_accountant),
    warehouse_id: int | None = Query(None),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
):
    return get_outstanding_summary(db, warehouse_id=warehouse_id, from_date=from_date, to_date=to_date)

@router.get("/collections")
def collections_endpoint(
    db: Session = Depends(get_db),
    current_user=Depends(require_accountant),
    warehouse_id: int | None = Query(None),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
    collected_by_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    items, total = get_collections(
        db, warehouse_id=warehouse_id, from_date=from_date, to_date=to_date,
        collected_by_id=collected_by_id, limit=limit, offset=offset,
    )
    return {"items": items, "total": total}

@router.get("/collections/summary")
def collection_summary_endpoint(
    db: Session = Depends(get_db),
    current_user=Depends(require_accountant),
    warehouse_id: int | None = Query(None),
    date: str | None = Query(None),
):
    return get_collection_summary(db, warehouse_id=warehouse_id, target_date=date)

@public_router.get("/credit-notes/{credit_note_id}/view", response_model=CreditNoteView)
def get_credit_note_view_endpoint(credit_note_id: int, db: Session = Depends(get_db), current_user = Depends(require_accountant)):
    try:
        return get_credit_note_view(db, credit_note_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
