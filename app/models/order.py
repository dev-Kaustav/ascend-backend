from sqlalchemy import Column, Integer, String, DateTime, Enum, ForeignKey, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.db.base import Base
from .enums import OrderStatus, PaymentStatus, IssueCategory

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    from_entity_type = Column(String, nullable=False)
    from_entity_id = Column(Integer, nullable=False)
    to_entity_type = Column(String, nullable=False)
    to_entity_id = Column(Integer, nullable=False)
    status = Column(Enum(OrderStatus, name="order_status"), default=OrderStatus.PENDING)
    payment_status = Column(Enum(PaymentStatus, name="payment_status"), default=PaymentStatus.CREDIT)
    invoice_number = Column(String, unique=True)
    beat_id = Column(Integer, ForeignKey("beats.id"), nullable=True)
    salesman_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    delivery_driver_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    delivery_date = Column(DateTime(timezone=True), nullable=True)
    panel_status = Column(String, nullable=True)
    issue_category = Column(Enum(IssueCategory, name="issue_category"), nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    credit_notes = relationship("CreditNote", back_populates="order", cascade="all, delete-orphan")
    payments = relationship("Account", back_populates="order", cascade="all, delete-orphan")
    trails = relationship("OrderTrail", backref="order", cascade="all, delete-orphan", order_by="OrderTrail.created_at")
