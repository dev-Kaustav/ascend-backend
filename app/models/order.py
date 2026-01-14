from sqlalchemy import Column, Integer, String, DateTime, Enum, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.db.base import Base
from .enums import OrderType, OrderStatus, PaymentStatus

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    order_type = Column(Enum(OrderType, name="order_type"), nullable=False)
    from_entity_type = Column(String, nullable=False)
    from_entity_id = Column(Integer, nullable=False)
    to_entity_type = Column(String, nullable=False)
    to_entity_id = Column(Integer, nullable=False)
    status = Column(Enum(OrderStatus, name="order_status"), default=OrderStatus.PENDING)
    payment_status = Column(Enum(PaymentStatus, name="payment_status"), default=PaymentStatus.UNPAID)
    invoice_number = Column(String, unique=True)
    salesman_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    credit_notes = relationship("CreditNote", back_populates="order", cascade="all, delete-orphan")
    payments = relationship("Account", back_populates="order", cascade="all, delete-orphan")
