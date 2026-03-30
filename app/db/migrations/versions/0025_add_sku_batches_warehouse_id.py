"""add warehouse id to sku batches"""

from alembic import op
import sqlalchemy as sa

revision = "0025_add_sku_batches_warehouse_id"
down_revision = "0024_access_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sku_batches", sa.Column("warehouse_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_sku_batches_warehouse_id_warehouses",
        "sku_batches",
        "warehouses",
        ["warehouse_id"],
        ["id"],
    )
    op.create_index("ix_sku_batches_warehouse_id", "sku_batches", ["warehouse_id"])

    op.execute(
        """
        UPDATE sku_batches
        SET warehouse_id = (
            SELECT warehouse_id
            FROM inventory_transactions
            WHERE inventory_transactions.batch_id = sku_batches.id
            LIMIT 1
        )
        WHERE warehouse_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_sku_batches_warehouse_id", table_name="sku_batches")
    op.drop_constraint("fk_sku_batches_warehouse_id_warehouses", "sku_batches", type_="foreignkey")
    op.drop_column("sku_batches", "warehouse_id")
