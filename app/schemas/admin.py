import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional
from datetime import datetime, date
from decimal import Decimal

from app.models.enums import INDIAN_STATES


MAX_URL_LENGTH = 2048
PACK_TYPES = ("box", "case", "ladi")


def validate_https_url(value):
    """Admin-entered image/icon links are rendered on the public storefront, so only plain
    https links with a host are stored (T-08-02: no javascript:, data:, http: or relative values).
    None or blank becomes None."""
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    if len(value) > MAX_URL_LENGTH or re.search(r"\s", value):
        raise ValueError("Must be an https:// link.")
    try:
        parts = urlsplit(value)
    except ValueError:
        raise ValueError("Must be an https:// link.")
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError("Must be an https:// link.")
    return value


def _normalize_pack_type(value):
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized not in PACK_TYPES:
        raise ValueError("Pack type must be box, case or ladi.")
    return normalized

class GroupCreate(BaseModel):
    name: str
    role: str

class GroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    role: str
    user_count: int = 0

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

# Partial update: every field optional so an omitted key means "leave alone". Subclassing the
# Create schema keeps its validators — Pydantic skips validation of defaults, so an omitted
# poc_phone_number does not trip the "required" branch of normalize_poc_phone_number.
class BrandUpdate(BrandCreate):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
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
    beat_id: Optional[int] = None

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
    beat_id: Optional[int] = None

class RetailerUpdate(RetailerCreate):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    mobile_number: Optional[int] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[int] = None
    gst_number: Optional[str] = None
    assigned_salesman_id: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    beat_id: Optional[int] = None


class RetailerBeatUpdate(BaseModel):
    # RPT-01 / D2: an absent beat_id and an explicit null mean the same thing here —
    # remove the retailer from its beat. This endpoint does one thing.
    beat_id: Optional[int] = None

class SKUCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    code: Optional[str] = None
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
    # Storefront catalogue data (STORE-07). All optional so existing callers are unaffected.
    image_url: Optional[str] = None
    category_id: Optional[int] = None
    net_weight_g: Optional[int] = Field(default=None, ge=1)
    pack_type: Optional[str] = None
    units_per_pack: Optional[int] = Field(default=None, ge=1)

    @field_validator("image_url")
    @classmethod
    def validate_image_url(cls, value):
        return validate_https_url(value)

    @field_validator("pack_type")
    @classmethod
    def validate_pack_type(cls, value):
        return _normalize_pack_type(value)

class SKUResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    code: Optional[str]
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
    image_url: Optional[str] = None
    category_id: Optional[int] = None
    net_weight_g: Optional[int] = None
    pack_type: Optional[str] = None
    units_per_pack: Optional[int] = None

class SKUUpdate(SKUCreate):
    model_config = ConfigDict(extra="ignore")
    name: Optional[str] = None
    code: Optional[str] = None
    brand_id: Optional[int] = None
    hsn_code: Optional[str] = None
    distributor_landing_price: Optional[float] = None
    mrp: Optional[float] = None
    discount_amount: Optional[float] = None
    discount_percent: Optional[float] = None
    rate: Optional[float] = None
    sgst_percent: Optional[float] = None
    sgst_amount: Optional[float] = None
    cgst_percent: Optional[float] = None
    cgst_amount: Optional[float] = None
    igst_percent: Optional[float] = None
    igst_amount: Optional[float] = None
    amount: Optional[float] = None
    weight: Optional[float] = None
    length_cm: Optional[float] = None
    width_cm: Optional[float] = None
    height_cm: Optional[float] = None


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=80)
    sort_order: int = 0
    icon_url: Optional[str] = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        if isinstance(value, str):
            value = value.strip()
        return value

    @field_validator("icon_url")
    @classmethod
    def validate_icon_url(cls, value):
        return validate_https_url(value)


class CategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # An omitted key leaves the column alone; an explicit null name is rejected (a category
    # always has a name), while an explicit null icon_url clears the icon.
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    sort_order: Optional[int] = None
    icon_url: Optional[str] = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        if isinstance(value, str):
            value = value.strip()
        return value

    @field_validator("name", "sort_order")
    @classmethod
    def reject_explicit_null(cls, value):
        if value is None:
            raise ValueError("This field cannot be null.")
        return value

    @field_validator("icon_url")
    @classmethod
    def validate_icon_url(cls, value):
        return validate_https_url(value)


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    sort_order: int
    icon_url: Optional[str] = None


class StoreSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # Partial: an omitted key means "leave it". An explicit null is rejected for the two money
    # fields (they are NOT NULL); a null storefront_warehouse_id closes the store.
    min_order_value: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    delivery_charge_percent: Optional[Decimal] = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    storefront_warehouse_id: Optional[int] = None

    @field_validator("min_order_value", "delivery_charge_percent")
    @classmethod
    def reject_explicit_null(cls, value):
        if value is None:
            raise ValueError("This field cannot be null.")
        return value


class StoreSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    min_order_value: float
    delivery_charge_percent: float
    storefront_warehouse_id: Optional[int] = None


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
    # Units the allocator would honour right now. Defined by the batch loop in
    # app/services/order.py, not by arithmetic over the columns above (INVT-07).
    available_quantity: float = 0
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
    bank_name: Optional[str] = None
    bank_account_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_ifsc: Optional[str] = None
    bank_branch: Optional[str] = None

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
    # Supplier fields still blank that a valid GST tax invoice needs. Computed, not
    # stored; the authority is invoice.missing_company_invoice_fields.
    missing_invoice_fields: list[str] = []
    # Read-only: the QR is changed by uploading a file, never by PATCHing this payload,
    # so it is deliberately absent from CompanyProfileBase.
    payment_qr_image_id: Optional[int] = None

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

class UserRoleUpdate(BaseModel):
    role: str

class UserGroupUpdate(BaseModel):
    group_id: Optional[int]

class UserPasswordUpdate(BaseModel):
    password: str
