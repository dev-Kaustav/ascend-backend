from sqlalchemy import Column, Integer, Boolean, ForeignKey, UniqueConstraint

from app.db.base import Base


class UserPermission(Base):
    __tablename__ = "user_permissions"
    __table_args__ = (UniqueConstraint("user_id", "permission_id", name="uq_user_permission"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    permission_id = Column(Integer, ForeignKey("permissions.id"), nullable=False)
    is_allowed = Column(Boolean, nullable=False, default=True)
