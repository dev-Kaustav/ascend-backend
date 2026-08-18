from sqlalchemy import CheckConstraint, Column, Integer, ForeignKey

from app.db.base import Base

class Inventory(Base):
    __tablename__ = "inventory"
    # INVT-02 / D4: the database's own floors on stock quantities — not a restatement of
    # the service-layer guards in app/services/order.py, which are bypassable by raw SQL,
    # an import script, or a future service that forgets them.
    __table_args__ = (
        CheckConstraint("total_quantity >= 0", name="ck_inventory_total_quantity_non_negative"),
        CheckConstraint("reserved_quantity >= 0", name="ck_inventory_reserved_quantity_non_negative"),
        CheckConstraint("reserved_quantity <= total_quantity", name="ck_inventory_reserved_not_over_total"),
    )

    sku_id = Column(Integer, ForeignKey("skus.id"), primary_key=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), primary_key=True)
    total_quantity = Column(Integer, default=0, nullable=False)
    reserved_quantity = Column(Integer, default=0, nullable=False)
