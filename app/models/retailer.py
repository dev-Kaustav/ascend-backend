from sqlalchemy import Column, Integer, String, ForeignKey, BigInteger, Float, DateTime, func

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
    # 08-08 / D-18: 'STORE' marks a self-signed-up shop so admins can find them.
    signup_source = Column(String(16), nullable=True)


class RetailerAddress(Base):
    """A saved delivery address for a storefront retailer (08-08, D-29).

    The Retailer row keeps its single registered address as the billing identity; these are the
    places a retailer can ask to have an order delivered. Latitude/longitude are stored exactly
    as the client geocoded them.
    """

    __tablename__ = "retailer_addresses"
    __table_args__ = (state_check_constraint("retailer_addresses"),)

    id = Column(Integer, primary_key=True)
    retailer_id = Column(Integer, ForeignKey("retailers.id"), nullable=False, index=True)
    label = Column(String(40), nullable=True)
    line1 = Column(String(200), nullable=False)
    line2 = Column(String(200), nullable=True)
    landmark = Column(String(120), nullable=True)
    city = Column(String(80), nullable=True)
    state = Column(String, nullable=False)
    pincode = Column(Integer, nullable=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
