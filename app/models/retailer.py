from sqlalchemy import Column, Integer, String, ForeignKey, BigInteger, Float

from app.db.base import Base
from .enums import state_check_constraint

class Retailer(Base):
    __tablename__ = "retailers"
    __table_args__ = (state_check_constraint("retailers"),)

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
    # RPT-01 / D2: beat membership is one beat per retailer, nullable because most
    # retailers are not on a beat. A retailer with beat_id IS NULL is excluded from
    # the planned set rather than defaulted onto one.
    beat_id = Column(
        Integer,
        ForeignKey("beats.id", name="fk_retailers_beat_id_beats"),
        nullable=True,
        index=True,
    )
