from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services.accounting import create_payment, create_credit_note, get_credit_note_view, list_payments, list_credit_notes
from app.schemas.accounting import PaymentCreate, PaymentResponse, CreditNoteCreate, CreditNoteResponse, CreditNoteView
from app.core.deps import require_accountant

router = APIRouter()
public_router = APIRouter()

@router.post("/orders/{order_id}/payments", response_model=PaymentResponse)
def create_payment_endpoint(order_id: int, payment: PaymentCreate, db: Session = Depends(get_db), current_user = Depends(require_accountant)):
    try:
        return create_payment(db, order_id, payment)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/credit-notes", response_model=CreditNoteResponse)
def create_credit_note_endpoint(credit_note: CreditNoteCreate, db: Session = Depends(get_db), current_user = Depends(require_accountant)):
    try:
        return create_credit_note(db, credit_note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/payments", response_model=list[PaymentResponse])
def list_payments_endpoint(db: Session = Depends(get_db), current_user = Depends(require_accountant)):
    return list_payments(db)

@router.get("/credit-notes", response_model=list[CreditNoteResponse])
def list_credit_notes_endpoint(db: Session = Depends(get_db), current_user = Depends(require_accountant)):
    return list_credit_notes(db)

@public_router.get("/credit-notes/{credit_note_id}/view", response_model=CreditNoteView)
def get_credit_note_view_endpoint(credit_note_id: int, db: Session = Depends(get_db), current_user = Depends(require_accountant)):
    try:
        return get_credit_note_view(db, credit_note_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
