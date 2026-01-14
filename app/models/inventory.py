from sqlalchemy import Column, Integer, Float, ForeignKey

from app.db.base import Base

class Inventory(Base):
    __tablename__ = "inventory"

    sku_id = Column(Integer, ForeignKey("skus.id"), primary_key=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), primary_key=True)
    total_quantity = Column(Float, default=0, nullable=False)
