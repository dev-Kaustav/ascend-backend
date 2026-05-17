from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from datetime import datetime

class OrderItemTaxCreate(BaseModel):
    tax_type: str
    rate: float

class OrderItemCreate(BaseModel):
    sku_id: int
    quantity: float
    unit_price: float
    discount_amount: float = 0
    taxes: list[OrderItemTaxCreate] = Field(default_factory=list)

class OrderCreate(BaseModel):
    retailer_id: int
    items: list[OrderItemCreate]
    warehouse_id: Optional[int] = None
    payment_mode: Optional[str] = None
    payment_amount: Optional[float] = None
    salesman_id: Optional[int] = None
    order_date: Optional[str] = None
    delivery_date: Optional[str] = None
    panel_status: Optional[str] = None
    issue_category: Optional[str] = None
    description: Optional[str] = None

class OrderItemTaxResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tax_type: str
    rate: float

class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sku_id: int
    sku_name: Optional[str] = None
    hsn_code: Optional[str] = None
    quantity: float
    unit_price: float
    discount_amount: float
    taxable_value: Optional[float] = None
    gst_amount: Optional[float] = None
    line_total: Optional[float] = None
    taxes: list[OrderItemTaxResponse] = []

class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    amount: float
    transaction_reference: str
    payment_mode: Optional[str] = None
    created_at: Optional[datetime] = None

class CreditNoteItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sku_id: int
    quantity: float
    unit_price: float

class CreditNoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    credit_note_number: str
    applies_to_outstanding: bool
    created_at: Optional[datetime] = None
    items: list[CreditNoteItemResponse] = []

class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    from_entity_type: str
    from_entity_id: int
    to_entity_type: str
    to_entity_id: int
    status: str
    invoice_number: Optional[str]
    salesman_id: Optional[int]
    delivery_driver_id: Optional[int] = None
    delivery_date: Optional[datetime] = None
    panel_status: Optional[str] = None
    issue_category: Optional[str] = None
    description: Optional[str] = None
    payment_status: Optional[str]
    items: list[OrderItemResponse]
    warehouse_name: Optional[str] = None
    warehouse_state: Optional[str] = None
    retailer_name: Optional[str] = None
    retailer_address_line1: Optional[str] = None
    retailer_city: Optional[str] = None
    retailer_state: Optional[str] = None
    retailer_pincode: Optional[int] = None
    retailer_gst_number: Optional[str] = None
    salesman_name: Optional[str] = None
    salesman_phone: Optional[int] = None
    delivery_driver_name: Optional[str] = None
    total_amount: Optional[float] = None
    pending_amount: Optional[float] = None
    taxable_value: Optional[float] = None
    gst_amount: Optional[float] = None
    subtotal: Optional[float] = None
    grand_total: Optional[float] = None
    payments: list[PaymentResponse] = []
    credit_notes: list[CreditNoteResponse] = []
    created_at: Optional[datetime] = None

class OrderListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    from_entity_type: str
    from_entity_id: int
    to_entity_type: str
    to_entity_id: int
    status: str
    invoice_number: Optional[str]
    salesman_id: Optional[int]
    delivery_driver_id: Optional[int] = None
    payment_status: Optional[str]
    created_at: Optional[datetime] = None
    total_amount: Optional[float] = None
    pending_amount: Optional[float] = None
    warehouse_name: Optional[str] = None
    retailer_name: Optional[str] = None
    salesman_name: Optional[str] = None

class OrderListPage(BaseModel):
    items: list[OrderListResponse]
    total: int

class StatusUpdate(BaseModel):
    status: str
    delivery_driver_id: Optional[int] = None
    payment_mode: Optional[str] = None
    payment_amount: Optional[float] = None
    payment_status: Optional[str] = None
    delivery_date: Optional[str] = None
    panel_status: Optional[str] = None
    issue_category: Optional[str] = None
    description: Optional[str] = None

class InvoiceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    order: OrderResponse
    taxable_value: float
    gst_amount: float
    subtotal: float
    grand_total: float
