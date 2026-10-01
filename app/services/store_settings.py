"""The only reader/writer of the single-row store_settings table.

A leaf module: it imports models only, so checkout, the catalogue and the admin router can all
depend on it without import cycles.
"""

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import StoreSettings, Warehouse

DEFAULT_MIN_ORDER_VALUE = Decimal("1000.00")
DEFAULT_DELIVERY_CHARGE_PERCENT = Decimal("8.00")


def get_store_settings(db: Session) -> StoreSettings:
    row = db.query(StoreSettings).order_by(StoreSettings.id.asc()).first()
    if row:
        return row
    row = StoreSettings(
        min_order_value=DEFAULT_MIN_ORDER_VALUE,
        delivery_charge_percent=DEFAULT_DELIVERY_CHARGE_PERCENT,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_store_settings(db: Session, payload) -> StoreSettings:
    row = get_store_settings(db)
    # exclude_unset: a body naming one field leaves the others alone. Do not copy
    # update_company_profile, which writes every column.
    data = payload.model_dump(exclude_unset=True)
    warehouse_id = data.get("storefront_warehouse_id")
    if warehouse_id is not None and not db.query(Warehouse.id).filter(Warehouse.id == warehouse_id).first():
        raise ValueError("Warehouse not found")
    for key, value in data.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row
