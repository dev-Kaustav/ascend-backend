from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.enums import AssignmentStatus


class OutletAssignment(Base):
    __tablename__ = "outlet_assignments"

    id = Column(Integer, primary_key=True, index=True)
    driver_employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    assigned_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String, nullable=False, default=AssignmentStatus.PENDING.value)
    note = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_outlet_assignments_driver_status", "driver_employee_id", "status"),
    )


class OutletAssignmentItem(Base):
    __tablename__ = "outlet_assignment_items"

    id = Column(Integer, primary_key=True, index=True)
    assignment_id = Column(Integer, ForeignKey("outlet_assignments.id", ondelete="CASCADE"), nullable=False)
    retailer_id = Column(Integer, ForeignKey("retailers.id"), nullable=False)

    __table_args__ = (
        Index("ix_outlet_assignment_items_assignment", "assignment_id"),
    )
