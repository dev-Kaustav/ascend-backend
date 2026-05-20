from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer
from sqlalchemy.sql import func

from app.db.base import Base


class OutletDelivery(Base):
    __tablename__ = "outlet_deliveries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    retailer_id = Column(Integer, ForeignKey("retailers.id"), nullable=False)
    driver_latitude = Column(Float, nullable=False)
    driver_longitude = Column(Float, nullable=False)
    driver_accuracy_m = Column(Float, nullable=True)
    distance_m = Column(Float, nullable=True)
    stored_lat_before = Column(Float, nullable=True)
    stored_lng_before = Column(Float, nullable=True)
    retailer_updated = Column(Boolean, nullable=False, default=False)
    outer_limit_overridden = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_outlet_deliveries_user_date", "user_id", "created_at"),
        Index("ix_outlet_deliveries_retailer_date", "retailer_id", "created_at"),
    )
