from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.admin import RetailerCreate


# Subclassing RetailerCreate keeps one copy of the mobile / pincode / state validators, so a
# request can never hold data the retailer it becomes would reject. The assignment fields are
# dropped: a salesman does not choose who a retailer belongs to, and beat is admin-only.
class RetailerRequestCreate(RetailerCreate):
    model_config = ConfigDict(extra="ignore")
    assigned_salesman_id: Optional[int] = None
    beat_id: Optional[int] = None
    mobile_number: Optional[int] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[int] = None
    gst_number: Optional[str] = None
    # Inherited from RetailerCreate, restated here only for symmetry with the fields above.
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class RetailerRequestUpdate(RetailerRequestCreate):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None


class RetailerRequestReview(BaseModel):
    model_config = ConfigDict(extra="ignore")
    note: Optional[str] = None


class RetailerRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    requested_by_employee_id: int
    requested_by_name: Optional[str] = None
    name: str
    mobile_number: Optional[int] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[int] = None
    gst_number: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    review_note: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_retailer_id: Optional[int] = None
    created_at: Optional[datetime] = None
