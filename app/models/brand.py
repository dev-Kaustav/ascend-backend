from sqlalchemy import Column, Integer, String, BigInteger

from app.db.base import Base

class Brand(Base):
    __tablename__ = "brands"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    poc_name = Column(String)
    poc_phone_number = Column(BigInteger)
    poc_email = Column(String)
