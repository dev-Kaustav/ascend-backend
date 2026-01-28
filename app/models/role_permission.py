from sqlalchemy import Column, Integer, ForeignKey, String, UniqueConstraint

from app.db.base import Base


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role", "permission_id", name="uq_role_permission"),)

    id = Column(Integer, primary_key=True, index=True)
    role = Column(String, nullable=False)
    permission_id = Column(Integer, ForeignKey("permissions.id"), nullable=False)
