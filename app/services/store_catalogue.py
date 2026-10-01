"""Read side of the public storefront catalogue (08-04).

Builds dedicated allowlist dicts from SKU/Brand/Category rows; ORM objects and order
serialisers never reach the response (PORT-06). Stock is only ever reduced to a boolean for an
anonymous visitor (D-08); a retailer additionally gets the trade price and an orderable maximum
(D-10, added with the retailer view).
"""
from decimal import Decimal

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models import Brand, Category, SKU
from app.services import inventory as inventory_service
from app.services.finance import _round_money
from app.services.store_settings import get_store_settings

# One line of a store order can never exceed this many packets; also caps max_orderable.
MAX_LINE_QUANTITY = 999


def store_visible_sku_criterion():
    """The listing rule (research A10, flagged): a SKU is in the store iff it has a category
    and 0 < amount <= mrp. An admin hides a SKU by clearing its category. The upper bound also
    guarantees D-31's discount (mrp - amount) is never negative."""
    return and_(
        SKU.category_id.isnot(None),
        SKU.mrp.isnot(None),
        SKU.amount.isnot(None),
        SKU.amount > 0,
        SKU.amount <= SKU.mrp,
    )


def pack_size_label(net_weight_g) -> str | None:
    """D-03: '23 g' below 1000 g, '1 kg' / '1.5 kg' from 1000 g up, None without a weight."""
    if net_weight_g is None:
        return None
    grams = int(net_weight_g)
    if grams < 1000:
        return f"{grams} g"
    kilos = (Decimal(grams) / Decimal(1000)).normalize()
    return f"{kilos:f} kg"


def storefront_availability(db: Session, sku_ids) -> dict[int, int]:
    """Allocatable units per SKU at the storefront warehouse, one inventory query for all ids.

    A NULL storefront_warehouse_id means the store is closed: nothing is available."""
    warehouse_id = get_store_settings(db).storefront_warehouse_id
    if warehouse_id is None:
        return {}
    return inventory_service.available_quantities(db, warehouse_id, sku_ids)


def _money(value) -> float:
    return float(_round_money(value))


def _product_dict(sku: SKU, brand: Brand, category: Category, available: int) -> dict:
    # Exactly the anonymous allowlist. Add keys here only after adding them to StoreProduct.
    return {
        "id": sku.id,
        "code": sku.code,
        "name": sku.name,
        "brand": {"id": brand.id, "name": brand.name},
        "category": {"id": category.id, "name": category.name, "icon_url": category.icon_url},
        "image_url": sku.image_url,
        "pack_size": pack_size_label(sku.net_weight_g),
        "mrp": _money(sku.mrp),
        "in_stock": available > 0,
    }


def list_store_products(db: Session, viewer, *, limit: int = 50, offset: int = 0):
    rows = (
        db.query(SKU, Brand, Category)
        .join(Brand, Brand.id == SKU.brand_id)
        .join(Category, Category.id == SKU.category_id)
        .filter(store_visible_sku_criterion())
        .all()
    )
    available = storefront_availability(db, [sku.id for sku, _, _ in rows])
    # D-11: out-of-stock products stay listed, after every in-stock one; then name, then id so
    # equal names have a stable order.
    rows.sort(key=lambda row: (available.get(row[0].id, 0) <= 0, (row[0].name or "").lower(), row[0].id))
    total = len(rows)
    page = rows[offset : offset + limit]
    items = [_product_dict(sku, brand, category, available.get(sku.id, 0)) for sku, brand, category in page]
    return items, total
