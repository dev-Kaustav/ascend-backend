from sqlalchemy import Column, Integer, String

from app.db.base import Base
from .enums import state_check_constraint

class Warehouse(Base):
    __tablename__ = "warehouses"
    __table_args__ = (state_check_constraint("warehouses"),)

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    location = Column(String)
    address_line1 = Column(String)
    address_line2 = Column(String)
    city = Column(String)
    state = Column(String)
    pincode = Column(Integer)
