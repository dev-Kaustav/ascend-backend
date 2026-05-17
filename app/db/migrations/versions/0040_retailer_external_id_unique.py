"""partial unique index on retailers.external_id

Revision ID: 0040_retailer_external_id_unique
Revises: 0039_drop_outlet_check_ins
Create Date: 2026-05-18
"""

from alembic import op
import sqlalchemy as sa

revision = "0040_retailer_external_id_unique"
down_revision = "0039_drop_outlet_check_ins"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    duplicates = bind.execute(sa.text("""
        SELECT external_id, COUNT(*) AS n
        FROM retailers
        WHERE external_id IS NOT NULL
        GROUP BY external_id
        HAVING COUNT(*) > 1
    """)).fetchall()
    if duplicates:
        preview = ", ".join(f"{eid!r} ({n})" for eid, n in duplicates[:10])
        raise RuntimeError(
            f"Cannot add unique index: {len(duplicates)} duplicate external_id value(s). "
            f"Sample: {preview}. Dedupe before re-running this migration."
        )

    op.drop_index("ix_retailers_external_id", table_name="retailers")
    op.execute(
        "CREATE UNIQUE INDEX ix_retailers_external_id "
        "ON retailers (external_id) WHERE external_id IS NOT NULL"
    )


def downgrade():
    op.drop_index("ix_retailers_external_id", table_name="retailers")
    op.create_index("ix_retailers_external_id", "retailers", ["external_id"])
