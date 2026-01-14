from sqlalchemy import Column, Integer, String, Enum, ForeignKey

from app.db.base import Base
from .enums import EmployeeRole

class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    role = Column(Enum(EmployeeRole, name="employee_role"), nullable=False)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=True)
