import re

from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional, List
from datetime import datetime, date

from app.models.enums import INDIAN_STATES

class PermissionEntry(BaseModel):
    code: str
    is_allowed: bool

class GroupCreate(BaseModel):
    name: str
    role: str

class GroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    role: str
    permissions: List[PermissionEntry] = []
    user_count: int = 0

class GroupPermissionUpdate(BaseModel):
    code: str
    is_allowed: bool

class BrandCreate(BaseModel):
    name: str
    poc_name: str
    poc_phone_number: int
    poc_email: Optional[str] = None

    @field_validator("poc_phone_number")
    @classmethod
    def normalize_poc_phone_number(cls, value):
        if value is None or value == "":
            raise ValueError("POC phone number is required.")
        raw = str(value)
        if not raw.isdigit():
            raise ValueError("POC phone number must be a 10 digit number.")
        if len(raw) != 10:
            raise ValueError("POC phone number must be 10 digits.")
        return int(raw)

class BrandResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    poc_name: Optional[str] = None
    poc_phone_number: Optional[int] = None
    poc_email: Optional[str] = None

class WarehouseCreate(BaseModel):
    name: str
    location: Optional[str]
    address_line1: Optional[str]
    address_line2: Optional[str]
    city: Optional[str]
    state: Optional[str]
    pincode: Optional[int]
    manager_id: int

    @field_validator("pincode")
    @classmethod
    def normalize_pincode(cls, value):
        if value is None or value == "":
            return None
        raw = str(value)
        if not raw.isdigit():
            raise ValueError("Pincode must be a 6 digit number.")
        if len(raw) != 6:
            raise ValueError("Pincode must be 6 digits.")
        return int(raw)

    @field_validator("state")
    @classmethod
    def normalize_state(cls, value):
        if value is None or value.strip() == "":
            return None
        value = value.strip()
        if value not in INDIAN_STATES:
            raise ValueError("State must be one of the 28 Indian states or 8 union territories.")
        return value

class WarehouseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    location: Optional[str]
    address_line1: Optional[str]
    address_line2: Optional[str]
    city: Optional[str]
    state: Optional[str]
    pincode: Optional[int]

class EmployeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str
    phone_number: Optional[int] = None
    role: str
    warehouse_id: Optional[int]

class RetailerCreate(BaseModel):
    name: str
    mobile_number: Optional[int]
    address_line1: Optional[str]
    address_line2: Optional[str]
    city: Optional[str]
    state: Optional[str]
    pincode: Optional[int]
    gst_number: Optional[str]
    assigned_salesman_id: Optional[int]
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @field_validator("mobile_number")
    @classmethod
    def normalize_mobile_number(cls, value):
        if value is None or value == "":
            return None
        raw = str(value)
        if not raw.isdigit():
            raise ValueError("Mobile number must be a 10 digit number.")
        if len(raw) != 10:
            raise ValueError("Mobile number must be 10 digits.")
        return int(raw)

    @field_validator("pincode")
    @classmethod
    def normalize_pincode(cls, value):
        if value is None or value == "":
            return None
        raw = str(value)
        if not raw.isdigit():
            raise ValueError("Pincode must be a 6 digit number.")
        if len(raw) != 6:
            raise ValueError("Pincode must be 6 digits.")
        return int(raw)

    @field_validator("state")
    @classmethod
    def normalize_state(cls, value):
        if value is None or value.strip() == "":
            return None
        value = value.strip()
        if value not in INDIAN_STATES:
            raise ValueError("State must be one of the 28 Indian states or 8 union territories.")
        return value

class RetailerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    mobile_number: Optional[int]
    address_line1: Optional[str]
    address_line2: Optional[str]
    city: Optional[str]
    state: Optional[str]
    pincode: Optional[int]
    gst_number: Optional[str]
    assigned_salesman_id: Optional[int]
    latitude: Optional[float]
    longitude: Optional[float]

class SKUCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    brand_id: int
    hsn_code: str
    distributor_landing_price: Optional[float] = None
    mrp: float
    discount_amount: float
    discount_percent: float
    rate: float
    sgst_percent: float
    sgst_amount: float
    cgst_percent: float
    cgst_amount: float
    igst_percent: float
    igst_amount: float
    amount: float
    weight: float
    length_cm: float
    width_cm: float
    height_cm: float

class SKUResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    brand_id: int
    hsn_code: Optional[str]
    distributor_landing_price: Optional[float]
    mrp: Optional[float]
    discount_amount: Optional[float]
    discount_percent: Optional[float]
    rate: Optional[float]
    sgst_percent: Optional[float]
    sgst_amount: Optional[float]
    cgst_percent: Optional[float]
    cgst_amount: Optional[float]
    igst_percent: Optional[float]
    igst_amount: Optional[float]
    amount: Optional[float]
    weight: Optional[float]
    length_cm: Optional[float]
    width_cm: Optional[float]
    height_cm: Optional[float]

class InventoryReceiptItem(BaseModel):
    sku_id: int
    quantity: float
    mfg_date: Optional[str]
    expiry_date: Optional[str]

class InventoryReceiptCreate(BaseModel):
    brand_id: int
    warehouse_id: int
    items: list[InventoryReceiptItem]

class InventoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    sku_id: int
    warehouse_id: int
    total_quantity: float
    reserved_quantity: float = 0
    earliest_expiry: Optional[date] = None
    expired_quantity: int = 0

class InventoryPage(BaseModel):
    items: list[InventoryResponse]
    total: int

class CompanyProfileBase(BaseModel):
    legal_name: str = "Ascend Foods"
    gstin: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    invoice_prefix: str = "ASC"
    invoice_footer: Optional[str] = None

class CompanyProfileUpdate(CompanyProfileBase):
    @field_validator("state")
    @classmethod
    def normalize_state(cls, value):
        if value is None or value.strip() == "":
            return None
        value = value.strip()
        if value not in INDIAN_STATES:
            raise ValueError("State must be one of the 28 Indian states or 8 union territories.")
        return value

class CompanyProfileResponse(CompanyProfileBase):
    model_config = ConfigDict(from_attributes=True)
    id: int

class UserCreate(BaseModel):
    email: str
    password: str
    role: Optional[str] = None
    group_id: Optional[int] = None
    employee_id: Optional[int] = None
    phone_number: int
    is_active: bool = True

    @field_validator("phone_number")
    @classmethod
    def normalize_phone_number(cls, value):
        if value is None or value == "":
            raise ValueError("Phone number is required.")
        raw = str(value)
        if not raw.isdigit():
            raise ValueError("Phone number must be a 10 digit number.")
        if len(raw) != 10:
            raise ValueError("Phone number must be 10 digits.")
        return int(raw)

class UserAdminResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    phone_number: Optional[int] = None
    role: str
    group_id: Optional[int] = None
    group_name: Optional[str] = None
    employee_id: Optional[int] = None
    is_active: bool
    created_at: datetime
    permissions: List[PermissionEntry] = []

class UserRoleUpdate(BaseModel):
    role: str

class UserPermissionUpdate(BaseModel):
    code: str
    is_allowed: bool

class UserGroupUpdate(BaseModel):
    group_id: Optional[int]

class UserPasswordUpdate(BaseModel):
    password: str
