"""Response schemas for the public storefront (PORT-06).

Explicit allowlists: a field exists here only if a storefront visitor may see it. Never build
these from Brand's contact fields, SKU cost/tax columns or any order serialiser.
"""
from typing import Optional

from pydantic import BaseModel


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
