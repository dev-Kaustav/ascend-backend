from sqlalchemy import Column, Integer, String, ForeignKey, BigInteger, Float

from app.db.base import Base

class Retailer(Base):
    __tablename__ = "retailers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    mobile_number = Column(BigInteger)
    address_line1 = Column(String)
    address_line2 = Column(String)
    city = Column(String)
    state = Column(String)
    pincode = Column(Integer)
    gst_number = Column(String)
    external_id = Column(String, nullable=True, unique=True, index=True)
    assigned_salesman_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
