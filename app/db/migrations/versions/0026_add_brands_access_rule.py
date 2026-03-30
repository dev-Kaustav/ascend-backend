"""add brands access rule"""

from alembic import op
import sqlalchemy as sa

revision = "0026_add_brands_access_rule"
down_revision = "0025_add_sku_batches_warehouse_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    existing = conn.execute(
        sa.text("SELECT 1 FROM access_rules WHERE path = :path"),
        {"path": "/app/brands"},
    ).fetchone()
    if existing:
        return

    access_rules = sa.table(
        "access_rules",
        sa.column("path", sa.String),
        sa.column("roles", sa.JSON),
        sa.column("permissions", sa.JSON),
    )
    op.bulk_insert(
        access_rules,
        [{"path": "/app/brands", "roles": ["ADMIN"], "permissions": []}],
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM access_rules WHERE path = :path"), {"path": "/app/brands"})
