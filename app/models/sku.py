from sqlalchemy import Column, Integer, String, ForeignKey, Float, Numeric

from app.db.base import Base

class SKU(Base):
    __tablename__ = "skus"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    hsn_code = Column(String)
    distributor_landing_price = Column(Numeric(12, 3))
    mrp = Column(Numeric(12, 3))
    discount_amount = Column(Numeric(12, 3), default=0)
    discount_percent = Column(Numeric(12, 3), default=0)
    rate = Column(Numeric(12, 3))
    sgst_percent = Column(Numeric(12, 3))
    sgst_amount = Column(Numeric(12, 3))
    cgst_percent = Column(Numeric(12, 3))
    cgst_amount = Column(Numeric(12, 3))
    igst_percent = Column(Numeric(12, 3))
    igst_amount = Column(Numeric(12, 3))
    amount = Column(Numeric(12, 3))
    weight = Column(Float)
    length_cm = Column(Float)
    width_cm = Column(Float)
    height_cm = Column(Float)
