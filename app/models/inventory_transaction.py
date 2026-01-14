from sqlalchemy import Column, Integer, Float, DateTime, Enum, ForeignKey
from sqlalchemy.sql import func

from app.db.base import Base
from .enums import TransactionType

class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"

    id = Column(Integer, primary_key=True, index=True)
    sku_id = Column(Integer, ForeignKey("skus.id"), nullable=False)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("sku_batches.id"), nullable=True)
    transaction_type = Column(Enum(TransactionType, name="transaction_type"), nullable=False)
    quantity = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
