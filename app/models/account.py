from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.db.base import Base
from .enums import PaymentMode

class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    transaction_reference = Column(String, unique=True, nullable=False)
    payment_mode = Column(Enum(PaymentMode, name="payment_mode"), nullable=True)
    collected_by_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    collection_date = Column(DateTime(timezone=True), nullable=True)
    cheque_number = Column(String, nullable=True)
    cheque_name = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    order = relationship("Order", back_populates="payments")
    collected_by = relationship("Employee", foreign_keys=[collected_by_id])
