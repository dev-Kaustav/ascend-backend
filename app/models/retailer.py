from sqlalchemy import Column, Integer, String, Text, ForeignKey

from app.db.base import Base

class Retailer(Base):
    __tablename__ = "retailers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    contact_info = Column(Text)
    assigned_salesman_id = Column(Integer, ForeignKey("employees.id"), nullable=True)