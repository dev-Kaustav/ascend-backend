from sqlalchemy import Column, DateTime, Index, Integer, String, func

from app.db.base import Base


class Category(Base):
    """Admin-managed storefront category (D-13). Names are unique case-insensitively."""

    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(80), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    icon_url = Column(String(2048))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# Case-insensitive uniqueness. Declared on the table after the class so the expression can
# reference the mapped column; create_all (tests, SQLite) then builds it like the migration does.
Index("uq_categories_name_lower", func.lower(Category.name), unique=True)
