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

class OrderItemTaxResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tax_type: str
    rate: float

class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sku_id: int
    quantity: float
    unit_price: float
    discount_amount: float
    taxes: list[OrderItemTaxResponse] = []

class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_type: str
    from_entity_type: str
    from_entity_id: int
    to_entity_type: str
    to_entity_id: int
    status: str
    invoice_number: Optional[str]
    salesman_id: Optional[int]
    payment_status: Optional[str]
    items: list[OrderItemResponse]
    created_at: Optional[datetime] = None

class StatusUpdate(BaseModel):
    status: str

class InvoiceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    order: OrderResponse
    taxable_value: float
    gst_amount: float
    subtotal: float
    grand_total: float
