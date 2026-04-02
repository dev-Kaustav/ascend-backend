from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.sql import func

from app.db.base import Base


class BillLabel(Base):
    __tablename__ = "bill_labels"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    bill_no = Column(String, nullable=True)
    invoice_date = Column(String, nullable=True)
    salesman_name = Column(String, nullable=True)
    beat = Column(String, nullable=True)
    gst_no = Column(String, nullable=True)
    outlet_name = Column(String, nullable=True)
    original_filename = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
