from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from datetime import datetime

class PaymentCreate(BaseModel):
    amount: float
    transaction_reference: str
    payment_mode: Optional[str] = None
    collected_by_id: Optional[int] = None
    collection_date: Optional[str] = None
    cheque_number: Optional[str] = None
    cheque_name: Optional[str] = None

class PaymentLine(BaseModel):
    amount: float
    payment_mode: str
    cheque_number: Optional[str] = None
    cheque_name: Optional[str] = None

class MultiPaymentCreate(BaseModel):
    order_id: int
    collected_by_id: Optional[int] = None
    collection_date: Optional[str] = None
    payments: list[PaymentLine]

class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_id: int
    amount: float
    transaction_reference: str
    payment_mode: Optional[str] = None
    collected_by_id: Optional[int] = None
    collection_date: Optional[datetime] = None
    cheque_number: Optional[str] = None
    cheque_name: Optional[str] = None
    created_at: Optional[datetime] = None

class PaymentDailyTotal(BaseModel):
    date: str
    amount: float

class PaymentPage(BaseModel):
    items: list[PaymentResponse]
    total: int
    total_amount: Optional[float] = None
    daily_totals: list[PaymentDailyTotal] = Field(default_factory=list)

class CreditNoteItemCreate(BaseModel):
    sku_id: int
    quantity: int
    unit_price: float

class CreditNoteItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sku_id: int
    quantity: float
    unit_price: float

class CreditNoteCreate(BaseModel):
    order_id: int
    items: list[CreditNoteItemCreate]
    restock: bool = False

class CreditNoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_id: int
    credit_note_number: str
    items: list[CreditNoteItemResponse]
    created_at: Optional[datetime] = None
    invoice_id: Optional[int] = None
    original_invoice_number: Optional[str] = None
    original_invoice_date: Optional[datetime] = None
    note_type: Optional[str] = None
    note_date: Optional[datetime] = None

class CreditNoteListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_id: int
    credit_note_number: str
    created_at: Optional[datetime] = None
    amount: Optional[float] = None
    invoice_id: Optional[int] = None
    original_invoice_number: Optional[str] = None
    original_invoice_date: Optional[datetime] = None

class CreditNotePage(BaseModel):
    items: list[CreditNoteListResponse]
    total: int
    total_amount: Optional[float] = None

class CreditNoteView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    credit_note: CreditNoteResponse
    taxable_value: float
    gst_amount: float
    subtotal: float
    grand_total: float
