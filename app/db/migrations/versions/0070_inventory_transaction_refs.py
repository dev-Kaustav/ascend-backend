"""Link stock movements to the order / credit note that caused them, for the stock ledger."""

from alembic import op
import sqlalchemy as sa

revision = "0070_inventory_transaction_refs"
down_revision = "0069_retailer_created_at"
branch_labels = None
depends_on = None


def upgrade():
    # Nullable with no backfill: older movements cannot be tied to an order reliably.
    op.add_column("inventory_transactions", sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=True))
    op.add_column("inventory_transactions", sa.Column("credit_note_id", sa.Integer(), sa.ForeignKey("credit_notes.id"), nullable=True))
    op.create_index("ix_inventory_transactions_order_id", "inventory_transactions", ["order_id"])


def downgrade():
    op.drop_index("ix_inventory_transactions_order_id", table_name="inventory_transactions")
    op.drop_column("inventory_transactions", "credit_note_id")
    op.drop_column("inventory_transactions", "order_id")
