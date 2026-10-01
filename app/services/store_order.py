"""Storefront checkout (08-09): quote, place and read a retailer's own orders.

The client sends sku ids, whole-packet quantities and a saved address id, nothing else. Price,
discount, tax, retailer, warehouse and salesman are all decided here (D-23, D-25, D-31, D-32),
and the order goes through the same create_outgoing_order every ops order uses, so reservation,
tax rows and the audit trail are not forked. Responses are built from dedicated allowlist dicts,
never from _serialize_order (PORT-06).
"""
from types import SimpleNamespace

from sqlalchemy.orm import Session, selectinload

from app.models import Order, OrderItem, Retailer, SKU, Warehouse
from app.schemas.order import OrderCreate, OrderItemCreate
from app.services import retailer_onboarding
from app.services.finance import (
    _round_money,
    calculate_order_item_totals,
    calculate_order_totals,
)
from app.services.order import (
    InsufficientStockError,
    _is_inter_state,
    _tax_rows_for_item,
    create_outgoing_order,
    scoped_orders_query,
)
from app.services.store_catalogue import (
    MAX_LINE_QUANTITY,
    pack_size_label,
    storefront_availability,
    store_visible_sku_criterion,
)
from app.services.store_settings import get_store_settings

MAX_CART_LINES = 50


class StoreClosed(Exception):
    """store_settings.storefront_warehouse_id is NULL: the store is not taking orders (D-32)."""


class UnknownProducts(Exception):
    """One or more sku ids are not store-visible products."""

    def __init__(self, sku_ids):
        super().__init__("Unknown products")
        self.sku_ids = list(sku_ids)


class StockChanged(Exception):
    """A line wants more than can be reserved. Carries only {sku_id, max_quantity} per line:
    the exception text of InsufficientStockError (warehouse name, counts) never reaches a
    retailer (T-08-27)."""

    def __init__(self, items):
        super().__init__("Stock changed")
        self.items = list(items)


class StoreOrderNotFound(Exception):
    """The order does not exist or belongs to another shop (indistinguishable on purpose)."""


class InvalidCart(ValueError):
    """A cart the validation layer could not catch on its own (duplicate lines merged above the
    per-line cap)."""


def _merge_lines(items) -> dict[int, int]:
    """Sum duplicate sku lines, keeping first-seen order, then enforce the per-line cap on the
    merged total so splitting a quantity across lines cannot get around it."""
    merged: dict[int, int] = {}
    for item in items:
        merged[item.sku_id] = merged.get(item.sku_id, 0) + item.quantity
    over = [sku_id for sku_id, quantity in merged.items() if quantity > MAX_LINE_QUANTITY]
    if over:
        raise InvalidCart(f"Quantity per product cannot exceed {MAX_LINE_QUANTITY}")
    return merged


def _price_lines(db: Session, merged: dict[int, int]) -> list[dict]:
    """Price each merged line from the SKU row (D-23, D-31). Only store-visible SKUs are
    orderable; SKU.rate is never read.

    unit_price = MRP; discount = (MRP - trade price) x quantity. This is the shape the salesman
    form stores, so a store order and an ops order for the same cart have the same grand_total.
    """
    skus = {
        sku.id: sku
        for sku in db.query(SKU).filter(SKU.id.in_(list(merged)), store_visible_sku_criterion()).all()
    }
    missing = [sku_id for sku_id in merged if sku_id not in skus]
    if missing:
        raise UnknownProducts(sorted(missing))
    lines = []
    for sku_id, quantity in merged.items():
        sku = skus[sku_id]
        lines.append(
            {
                "sku": sku,
                "quantity": quantity,
                "unit_price": _round_money(sku.mrp),
                "discount": _round_money((sku.mrp - sku.amount) * quantity),
            }
        )
    return lines


def _open_warehouse(db: Session) -> Warehouse:
    warehouse_id = get_store_settings(db).storefront_warehouse_id
    if warehouse_id is None:
        raise StoreClosed()
    warehouse = db.query(Warehouse).filter(Warehouse.id == warehouse_id).first()
    if warehouse is None:
        raise StoreClosed()
    return warehouse


def _orderable_cap(available: dict[int, int], sku_id: int) -> int:
    return min(max(available.get(sku_id, 0), 0), MAX_LINE_QUANTITY)


def _stock_conflict(merged: dict[int, int], available: dict[int, int], *, only_short: bool) -> StockChanged:
    """Build the sanitized conflict from computed availability, never from exception text.
    only_short lists the lines that exceed it; otherwise (a race the pre-check could not see)
    every line is listed with its current cap so the client can re-fit the whole cart."""
    items = []
    for sku_id, quantity in merged.items():
        cap = _orderable_cap(available, sku_id)
        if quantity > cap or not only_short:
            items.append({"sku_id": sku_id, "max_quantity": cap})
    return StockChanged(items)


def _quote_item(sku: SKU, line: dict, inter_state: bool):
    """A lightweight stand-in for an OrderItem carrying what finance reads: quantity, the stored
    unit_price/discount and the tax rows create_outgoing_order would persist for this SKU."""
    taxes = _tax_rows_for_item(sku, SimpleNamespace(taxes=[]), inter_state)
    return SimpleNamespace(
        sku_id=sku.id,
        quantity=line["quantity"],
        unit_price=line["unit_price"],
        discount_amount=line["discount"],
        taxes=[SimpleNamespace(rate=tax["rate"]) for tax in taxes],
    )


def quote_cart(db: Session, user, payload) -> dict:
    """Server-true prices for a cart, computed by the same finance functions the order will be
    priced with (STORE-06 precision), so a quote's total equals the placed order's grand_total."""
    warehouse = _open_warehouse(db)
    merged = _merge_lines(payload.items)
    lines = _price_lines(db, merged)
    retailer = db.query(Retailer).filter(Retailer.id == user.retailer_id).first()
    inter_state = _is_inter_state(warehouse, retailer)
    available = storefront_availability(db, list(merged))

    items = [_quote_item(line["sku"], line, inter_state) for line in lines]
    out_lines = []
    for line, item in zip(lines, items):
        sku = line["sku"]
        cap = _orderable_cap(available, sku.id)
        out_lines.append(
            {
                "sku_id": sku.id,
                "name": sku.name,
                "image_url": sku.image_url,
                "pack_size": pack_size_label(sku.net_weight_g),
                "mrp": float(line["unit_price"]),
                "trade_price": float(_round_money(sku.amount)),
                "quantity": line["quantity"],
                "line_total": float(calculate_order_item_totals(item)["line_total"]),
                "max_orderable": cap,
                "within_stock": line["quantity"] <= cap,
            }
        )
    totals = calculate_order_totals(SimpleNamespace(items=items))
    return {
        "lines": out_lines,
        "subtotal": float(totals["subtotal"]),
        "total": float(totals["grand_total"]),
    }


def place_store_order(db: Session, user, payload) -> Order:
    warehouse = _open_warehouse(db)
    address = retailer_onboarding._get_address(db, user.retailer_id, payload.address_id)
    merged = _merge_lines(payload.items)
    lines = _price_lines(db, merged)

    # Cheap early refusal, before any lock is taken. Not the authority: the FOR UPDATE
    # reservation inside create_outgoing_order decides, and its refusal is mapped below.
    available = storefront_availability(db, list(merged))
    if any(quantity > _orderable_cap(available, sku_id) for sku_id, quantity in merged.items()):
        raise _stock_conflict(merged, available, only_short=True)

    # Nothing from the request body is trusted beyond sku ids, quantities and the address id.
    order_create = OrderCreate(
        retailer_id=user.retailer_id,
        warehouse_id=warehouse.id,
        items=[
            OrderItemCreate(
                sku_id=line["sku"].id,
                quantity=line["quantity"],
                unit_price=float(line["unit_price"]),
                discount_amount=float(line["discount"]),
            )
            for line in lines
        ],
    )
    ship_to = {
        "delivery_address_id": address.id,
        "ship_to_label": address.label,
        "ship_to_line1": address.line1,
        "ship_to_line2": address.line2,
        "ship_to_landmark": address.landmark,
        "ship_to_city": address.city,
        "ship_to_state": address.state,
        "ship_to_pincode": address.pincode,
        "ship_to_latitude": address.latitude,
        "ship_to_longitude": address.longitude,
    }
    try:
        return create_outgoing_order(db, order_create, user, ship_to=ship_to)
    except InsufficientStockError:
        # create_outgoing_order already rolled back; availability is re-read, the text is dropped.
        raise _stock_conflict(merged, storefront_availability(db, list(merged)), only_short=False)


def _detail(db: Session, order: Order) -> dict:
    sku_ids = [item.sku_id for item in order.items]
    skus = {sku.id: sku for sku in db.query(SKU).filter(SKU.id.in_(sku_ids)).all()} if sku_ids else {}
    lines = []
    for item in sorted(order.items, key=lambda i: i.id):
        sku = skus.get(item.sku_id)
        lines.append(
            {
                "sku_id": item.sku_id,
                "name": sku.name if sku else None,
                "image_url": sku.image_url if sku else None,
                "pack_size": pack_size_label(sku.net_weight_g) if sku else None,
                "quantity": item.quantity,
                "mrp": float(_round_money(item.unit_price)),
                "line_total": float(calculate_order_item_totals(item)["line_total"]),
            }
        )
    totals = calculate_order_totals(order)
    return {
        "id": order.id,
        "status": order.status.value if hasattr(order.status, "value") else order.status,
        "created_at": order.created_at,
        "lines": lines,
        "subtotal": float(totals["subtotal"]),
        "total": float(totals["grand_total"]),
        "ship_to": {
            "label": order.ship_to_label,
            "line1": order.ship_to_line1,
            "line2": order.ship_to_line2,
            "landmark": order.ship_to_landmark,
            "city": order.ship_to_city,
            "state": order.ship_to_state,
            "pincode": order.ship_to_pincode,
        },
        "invoice_available": order.invoice is not None,
    }


def _own_orders(db: Session, user):
    return scoped_orders_query(db, user).options(
        selectinload(Order.items).selectinload(OrderItem.taxes),
        selectinload(Order.invoice),
    )


def get_store_order(db: Session, user, order_id: int) -> dict:
    order = _own_orders(db, user).filter(Order.id == order_id).first()
    if order is None:
        raise StoreOrderNotFound(order_id)
    return _detail(db, order)


def get_store_order_for_invoice(db: Session, user, order_id: int) -> Order:
    """The caller's own order as an ORM row (for its invoice), or StoreOrderNotFound."""
    order = scoped_orders_query(db, user).filter(Order.id == order_id).first()
    if order is None:
        raise StoreOrderNotFound(order_id)
    return order


def list_store_orders(db: Session, user, limit: int, offset: int) -> dict:
    total = scoped_orders_query(db, user).count()
    orders = (
        _own_orders(db, user)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "items": [
            {
                "id": order.id,
                "status": order.status.value if hasattr(order.status, "value") else order.status,
                "created_at": order.created_at,
                "item_count": len(order.items),
                "total": float(calculate_order_totals(order)["grand_total"]),
                "invoice_available": order.invoice is not None,
            }
            for order in orders
        ],
        "total": total,
    }
