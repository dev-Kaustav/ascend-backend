from sqlalchemy import Column, DateTime, Integer, String, JSON
from sqlalchemy.sql import func

from app.db.base import Base


class AccessRule(Base):
    __tablename__ = "access_rules"

    id = Column(Integer, primary_key=True, index=True)
    path = Column(String, unique=True, nullable=False, index=True)
    roles = Column(JSON, nullable=True)
    permissions = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
