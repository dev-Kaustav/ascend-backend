from typing import Optional
from pydantic import BaseModel, Field


class RetailerLocation(BaseModel):
    external_id: str
    name: str
    address: Optional[str]
    latitude: float
    longitude: float


class RetailerStub(BaseModel):
    external_id: str
    name: str


class RetailerLookupRequest(BaseModel):
    external_ids: list[str] = Field(..., min_length=1, max_length=200)


class RetailerLookupResponse(BaseModel):
    found: list[RetailerLocation]
    missing_coords: list[RetailerStub]
    not_found: list[str]


class MarkDeliveredRequest(BaseModel):
    retailer_external_id: str
    latitude: float
    longitude: float
    accuracy_m: Optional[float] = None
    confirm_outer_limit: bool = False


class MarkDeliveredResponse(BaseModel):
    delivered: bool
    distance_m: Optional[float] = None
    retailer_updated: bool
    new_latitude: Optional[float] = None
    new_longitude: Optional[float] = None
    requires_confirmation: bool = False
    message: Optional[str] = None
