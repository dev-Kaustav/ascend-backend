from sqlalchemy import Column, Integer, DateTime, Enum, ForeignKey
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
    quantity = Column(Integer, nullable=False)
    # What caused the movement, for the stock ledger: the receipt or sales order, and the
    # credit note for a restock. Null on rows written before these columns existed.
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, index=True)
    credit_note_id = Column(Integer, ForeignKey("credit_notes.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
