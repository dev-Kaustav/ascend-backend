from sqlalchemy import Column, Integer, String, Enum, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base
from .enums import EmployeeRole


class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    role = Column(Enum(EmployeeRole, name="employee_role"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    users = relationship("User", back_populates="group")
