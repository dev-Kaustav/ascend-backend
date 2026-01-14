from sqlalchemy import Column, Integer, String, Text, ForeignKey

from app.db.base import Base

class SKU(Base):
    __tablename__ = "skus"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    description = Column(Text)
    unit = Column(String)