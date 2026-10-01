from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, Numeric, func

from app.db.base import Base


class StoreSettings(Base):
    """Single-row storefront configuration (D-04, D-05, D-32).

    A table of its own rather than company_profile columns: update_company_profile rewrites
    every column of that row, so a profile save would silently reset these (research Pitfall 4).
    A NULL storefront_warehouse_id means the store is closed.
    """

    __tablename__ = "store_settings"
    __table_args__ = (
        CheckConstraint("min_order_value >= 0", name="ck_store_settings_min_order_value_nonnegative"),
        CheckConstraint(
            "delivery_charge_percent >= 0 AND delivery_charge_percent <= 100",
            name="ck_store_settings_delivery_charge_percent_range",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    min_order_value = Column(Numeric(12, 2), nullable=False, default=1000, server_default="1000.00")
    delivery_charge_percent = Column(Numeric(5, 2), nullable=False, default=8, server_default="8.00")
    storefront_warehouse_id = Column(Integer, ForeignKey("warehouses.id", ondelete="SET NULL"))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
