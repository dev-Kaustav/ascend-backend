from sqlalchemy import Column, Integer, String, ForeignKey, Float

from app.db.base import Base

class SKU(Base):
    __tablename__ = "skus"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    hsn_code = Column(String)
    pack_quantity = Column(Float)
    mrp = Column(Float)
    discount_amount = Column(Float, default=0)
    discount_percent = Column(Float, default=0)
    rate = Column(Float)
    sgst_percent = Column(Float)
    sgst_amount = Column(Float)
    cgst_percent = Column(Float)
    cgst_amount = Column(Float)
    igst_percent = Column(Float)
    igst_amount = Column(Float)
    amount = Column(Float)
    weight = Column(Float)
    length_cm = Column(Float)
    width_cm = Column(Float)
    height_cm = Column(Float)
