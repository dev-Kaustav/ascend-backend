from sqlalchemy import Column, Integer, Boolean, ForeignKey, UniqueConstraint

from app.db.base import Base


class GroupPermission(Base):
    __tablename__ = "group_permissions"
    __table_args__ = (UniqueConstraint("group_id", "permission_id", name="uq_group_permission"),)

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=False)
    permission_id = Column(Integer, ForeignKey("permissions.id"), nullable=False)
    is_allowed = Column(Boolean, nullable=False, default=True)
