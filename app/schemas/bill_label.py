from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class BillLabelCreate(BaseModel):
    bill_no: Optional[str] = None
    invoice_date: Optional[str] = None
    salesman_name: Optional[str] = None
    beat: Optional[str] = None
    gst_no: Optional[str] = None
    outlet_name: Optional[str] = None
    original_filename: Optional[str] = None


class BillLabelUpdate(BaseModel):
    bill_no: Optional[str] = None
    invoice_date: Optional[str] = None
    salesman_name: Optional[str] = None
    beat: Optional[str] = None
    gst_no: Optional[str] = None
    outlet_name: Optional[str] = None


class BillLabelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    bill_no: Optional[str] = None
    invoice_date: Optional[str] = None
    salesman_name: Optional[str] = None
    beat: Optional[str] = None
    gst_no: Optional[str] = None
    outlet_name: Optional[str] = None
    original_filename: Optional[str] = None
    created_at: Optional[datetime] = None


class BillLabelPage(BaseModel):
    items: list[BillLabelResponse]
    total: int
