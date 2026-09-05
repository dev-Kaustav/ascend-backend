from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.enums import RetailerRequestStatus


class RetailerRequest(Base):
    """A salesman's proposal for a new outlet, pending an admin's decision.

    The retailer columns are duplicated here rather than referenced, because no Retailer row
    exists until approval — that is the whole point. A salesman inventing a shop must not be
    able to book orders against it, so the proposal lives in its own table until an admin
    turns it into a real retailer.
    """

    __tablename__ = "retailer_requests"

    id = Column(Integer, primary_key=True, index=True)
    requested_by_employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    status = Column(String, nullable=False, default=RetailerRequestStatus.PENDING.value)

    name = Column(String, nullable=False)
    # BigInteger, matching retailers.mobile_number: a 10-digit Indian mobile is ~9.8e9 and
    # overflows PostgreSQL's 4-byte integer. SQLite stores it either way, so the type has to be
    # right here rather than discovered in production.
    mobile_number = Column(BigInteger, nullable=True)
    address_line1 = Column(String, nullable=True)
    address_line2 = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    pincode = Column(Integer, nullable=True)
    gst_number = Column(String, nullable=True)

    reviewed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_note = Column(String, nullable=True)
    # Audit link from an approved request to the row it produced.
    created_retailer_id = Column(Integer, ForeignKey("retailers.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_retailer_requests_status_requester", "status", "requested_by_employee_id"),
    )
