from sqlalchemy import BigInteger, Column, DateTime, Integer, String
from sqlalchemy.sql import func

from app.db.base import Base


class LandingLead(Base):
    """An enquiry filed from the public marketing site by someone with no account.

    Deliberately not a `RetailerRequest`: that table models a salesman's proposal and its
    `requested_by_employee_id` is NOT NULL on purpose. A stranger on the website is not an
    employee, and widening that column to hold one would let an unauthenticated caller write
    rows into the approval queue that turns into real retailers. This table is a plain
    inbox instead — nothing downstream reads it, an admin does.
    """

    __tablename__ = "landing_leads"

    id = Column(Integer, primary_key=True, index=True)
    # "RETAILER" (shop wanting to stock) or "BRAND" (brand wanting distribution). Plain string
    # rather than an enum: the marketing site changes its form faster than a migration lands.
    lead_type = Column(String, nullable=False)
    name = Column(String, nullable=False)
    # BigInteger matching retailers.mobile_number — a 10-digit Indian mobile overflows a
    # 4-byte integer in PostgreSQL.
    mobile_number = Column(BigInteger, nullable=False)
    pincode = Column(Integer, nullable=True)
    note = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
