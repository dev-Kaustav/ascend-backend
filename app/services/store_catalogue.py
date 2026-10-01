"""Read side of the public storefront catalogue (08-04).

Builds dedicated allowlist dicts from SKU/Brand/Category rows; ORM objects and order
serialisers never reach the response (PORT-06). Stock is only ever reduced to a boolean for an
anonymous visitor (D-08); a retailer additionally gets the trade price and an orderable maximum
(D-10, added with the retailer view).
"""
from decimal import Decimal

from sqlalchemy import and_, func, or_
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


class StoreProductNotFound(Exception):
    pass


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


def list_store_categories(db: Session) -> list[dict]:
    """Categories that hold at least one visible SKU, in admin order (sort_order, id)."""
    rows = (
        db.query(Category)
        .filter(
            db.query(SKU.id)
            .filter(SKU.category_id == Category.id, store_visible_sku_criterion())
            .exists()
        )
        .order_by(Category.sort_order.asc(), Category.id.asc())
        .all()
    )
    return [{"id": c.id, "name": c.name, "icon_url": c.icon_url} for c in rows]


def list_store_brands(db: Session) -> list[dict]:
    """Brands that hold at least one visible SKU, as {id, name} only (never the POC fields)."""
    rows = (
        db.query(Brand.id, Brand.name)
        .filter(
            db.query(SKU.id)
            .filter(SKU.brand_id == Brand.id, store_visible_sku_criterion())
            .exists()
        )
        .order_by(func.lower(Brand.name).asc(), Brand.id.asc())
        .all()
    )
    return [{"id": brand_id, "name": name} for brand_id, name in rows]


def _visible_rows_query(db: Session):
    return (
        db.query(SKU, Brand, Category)
        .join(Brand, Brand.id == SKU.brand_id)
        .join(Category, Category.id == SKU.category_id)
        .filter(store_visible_sku_criterion())
    )


def list_store_products(
    db: Session,
    viewer,
    *,
    category_id: int | None = None,
    brand_id: int | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    query = _visible_rows_query(db)
    if category_id is not None:
        query = query.filter(SKU.category_id == category_id)
    if brand_id is not None:
        query = query.filter(SKU.brand_id == brand_id)
    term = (q or "").strip().lower()
    if term:
        # autoescape makes '%' and '_' in the visitor's text literal characters, not wildcards.
        query = query.filter(
            or_(
                func.lower(SKU.name).contains(term, autoescape=True),
                func.lower(SKU.code).contains(term, autoescape=True),
            )
        )
    rows = query.all()
    available = storefront_availability(db, [sku.id for sku, _, _ in rows])
    # D-11: out-of-stock products stay listed, after every in-stock one; then name, then id so
    # equal names have a stable order. The catalogue is small, so this sorts in Python.
    rows.sort(key=lambda row: (available.get(row[0].id, 0) <= 0, (row[0].name or "").lower(), row[0].id))
    total = len(rows)
    page = rows[offset : offset + limit]
    items = [_product_dict(sku, brand, category, available.get(sku.id, 0)) for sku, brand, category in page]
    return items, total


def get_store_product(db: Session, viewer, product_id: int) -> dict:
    row = _visible_rows_query(db).filter(SKU.id == product_id).first()
    if row is None:
        raise StoreProductNotFound(product_id)
    sku, brand, category = row
    available = storefront_availability(db, [sku.id])
    return _product_dict(sku, brand, category, available.get(sku.id, 0))
