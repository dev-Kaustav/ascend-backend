from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from datetime import datetime

class PaymentCreate(BaseModel):
    amount: float
    transaction_reference: str

class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_id: int
    amount: float
    transaction_reference: str
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
    quantity: float
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

class CreditNoteListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_id: int
    credit_note_number: str
    created_at: Optional[datetime] = None
    amount: Optional[float] = None

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
