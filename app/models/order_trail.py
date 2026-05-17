from sqlalchemy import Column, Integer, String, DateTime, Enum, ForeignKey, Text
from sqlalchemy.sql import func

from app.db.base import Base
from .enums import OrderStatus


class OrderTrail(Base):
    __tablename__ = "order_trails"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    order_status = Column(Enum(OrderStatus, name="order_status"), nullable=False)
    description = Column(Text, nullable=True)
    changed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
