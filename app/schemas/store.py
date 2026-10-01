"""Response schemas for the public storefront (PORT-06).

Explicit allowlists: a field exists here only if a storefront visitor may see it. Never build
these from Brand's contact fields, SKU cost/tax columns or any order serialiser.
"""
import re
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import INDIAN_STATES
from app.schemas.auth import RetailerShopPrefill
from app.services.store_catalogue import MAX_LINE_QUANTITY
from app.services.store_order import MAX_CART_LINES


class StoreCategory(BaseModel):
    id: int
    name: str
    icon_url: Optional[str] = None


class StoreBrand(BaseModel):
    id: int
    name: str


class StoreProduct(BaseModel):
    id: int
    code: Optional[str] = None
    name: str
    brand: StoreBrand
    category: StoreCategory
    image_url: Optional[str] = None
    pack_size: Optional[str] = None
    mrp: float
    in_stock: bool
    # Retailer-only (D-10): omitted from the JSON for every other viewer.
    trade_price: Optional[float] = None
    max_orderable: Optional[int] = None


class StoreProductPage(BaseModel):
    items: list[StoreProduct]
    total: int


# --- Onboarding and saved delivery addresses (08-08) ------------------------------------------
# The backend stores what the client geocoded; it only checks that the values cannot break an
# invoice later (D-27): a state outside INDIAN_STATES would fail the place_of_supply constraint.

_GSTIN = re.compile(r"[A-Z0-9]{15}")


def _blank_to_none(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _check_state(value):
    if value is None:
        return value
    value = value.strip()
    if value not in INDIAN_STATES:
        raise ValueError("State must be one of the 28 Indian states or 8 union territories.")
    return value


def _check_pincode(value):
    if value is None:
        return None
    raw = str(value).strip()
    if raw == "":
        return None
    if not (raw.isdigit() and raw.isascii() and len(raw) == 6):
        raise ValueError("Pincode must be 6 digits.")
    return int(raw)


def _check_line1(value):
    if value is None:
        return value
    value = value.strip()
    if not value:
        raise ValueError("Address line 1 is required.")
    return value


class _AddressValidators(BaseModel):
    """Field checks shared by StoreAddressIn and StoreAddressUpdate."""

    @field_validator("line1", check_fields=False)
    @classmethod
    def _line1(cls, value):
        return _check_line1(value)

    @field_validator("state", check_fields=False)
    @classmethod
    def _state(cls, value):
        return _check_state(value)

    @field_validator("pincode", check_fields=False)
    @classmethod
    def _pincode(cls, value):
        return _check_pincode(value)

    @field_validator("label", "line2", "landmark", "city", mode="before", check_fields=False)
    @classmethod
    def _optional_text(cls, value):
        return _blank_to_none(value)


class StoreAddressIn(_AddressValidators):
    label: Optional[str] = Field(None, max_length=40)
    line1: str = Field(..., max_length=200)
    line2: Optional[str] = Field(None, max_length=200)
    landmark: Optional[str] = Field(None, max_length=120)
    city: Optional[str] = Field(None, max_length=80)
    state: str
    pincode: Optional[int | str] = None
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)



class StoreAddressUpdate(_AddressValidators):
    """Partial update: an omitted key leaves the field alone; line1, state and the coordinates
    can be changed but never cleared."""

    label: Optional[str] = Field(None, max_length=40)
    line1: Optional[str] = Field(None, max_length=200)
    line2: Optional[str] = Field(None, max_length=200)
    landmark: Optional[str] = Field(None, max_length=120)
    city: Optional[str] = Field(None, max_length=80)
    state: Optional[str] = None
    pincode: Optional[int | str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)


    @model_validator(mode="after")
    def _required_fields_not_cleared(self):
        for name in ("line1", "state", "latitude", "longitude"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be cleared.")
        return self


class StoreAddress(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    label: Optional[str] = None
    line1: str
    line2: Optional[str] = None
    landmark: Optional[str] = None
    city: Optional[str] = None
    state: str
    pincode: Optional[int] = None
    latitude: float
    longitude: float


class StoreShopIn(BaseModel):
    shop_name: str = Field(..., max_length=120)
    gst_number: Optional[str] = None
    address: Optional[StoreAddressIn] = None

    @field_validator("shop_name")
    @classmethod
    def _shop_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Shop name is required.")
        return value

    @field_validator("gst_number")
    @classmethod
    def _gst_number(cls, value):
        value = _blank_to_none(value)
        if value is None:
            return None
        value = value.upper()
        if not _GSTIN.fullmatch(value):
            raise ValueError("GST number must be 15 letters or digits.")
        return value


class StoreShop(BaseModel):
    model_config = {"from_attributes": True}

    retailer_id: int
    name: str
    gst_number: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[int] = None


class StoreMe(BaseModel):
    mobile: Optional[int] = None
    onboarding: Literal["new", "confirm", "ready"]
    shop: Optional[StoreShop] = None
    prefill: Optional[RetailerShopPrefill] = None
    addresses: list[StoreAddress] = []


# --- Checkout (08-09) ---------------------------------------------------------------------------
# Requests carry sku ids, whole-packet quantities and an address id and nothing else. Pydantic's
# default extra="ignore" drops any client-sent price, tax, retailer or warehouse field: criterion
# 3 asks for those to be ignored, not rejected. Do not set extra="forbid" here.


class StoreCartLineIn(BaseModel):
    sku_id: int
    quantity: int = Field(..., ge=1, le=MAX_LINE_QUANTITY)


class StoreOrderIn(BaseModel):
    items: list[StoreCartLineIn] = Field(..., min_length=1, max_length=MAX_CART_LINES)
    address_id: int


class StoreShipTo(BaseModel):
    label: Optional[str] = None
    line1: Optional[str] = None
    line2: Optional[str] = None
    landmark: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[int] = None


class StoreOrderLine(BaseModel):
    sku_id: int
    name: Optional[str] = None
    image_url: Optional[str] = None
    pack_size: Optional[str] = None
    quantity: int
    mrp: float
    line_total: float


class StoreOrderDetail(BaseModel):
    id: int
    status: str
    created_at: Optional[datetime] = None
    lines: list[StoreOrderLine]
    subtotal: float
    total: float
    ship_to: StoreShipTo
    invoice_available: bool


class StoreCartIn(BaseModel):
    items: list[StoreCartLineIn] = Field(..., min_length=1, max_length=MAX_CART_LINES)


class StoreQuoteLine(BaseModel):
    sku_id: int
    name: Optional[str] = None
    image_url: Optional[str] = None
    pack_size: Optional[str] = None
    mrp: float
    trade_price: float
    quantity: int
    line_total: float
    max_orderable: int
    within_stock: bool


class StoreQuote(BaseModel):
    lines: list[StoreQuoteLine]
    subtotal: float
    total: float


class StoreOrderSummary(BaseModel):
    id: int
    status: str
    created_at: Optional[datetime] = None
    item_count: int
    total: float
    invoice_available: bool


class StoreOrderPage(BaseModel):
    items: list[StoreOrderSummary]
    total: int


class StoreStockItem(BaseModel):
    sku_id: int
    max_quantity: int


class StoreStockConflict(BaseModel):
    """409 body for a cart that no longer fits the stock. Carries the per-line cap only: no
    warehouse name, no free-text count (D-08, D-09, T-08-27)."""

    code: Literal["STOCK_CHANGED"] = "STOCK_CHANGED"
    message: str
    items: list[StoreStockItem]
