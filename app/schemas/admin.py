import re

from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional, List
from datetime import datetime

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
    contact_info: Optional[str]

class BrandResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    contact_info: Optional[str]

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

class SKUCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    brand_id: int
    hsn_code: str
    pack_quantity: float
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
    pack_quantity: Optional[float]
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

class InventoryPage(BaseModel):
    items: list[InventoryResponse]
    total: int

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
