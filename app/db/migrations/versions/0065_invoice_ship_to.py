"""Ship To snapshot on invoices (08-10, D-29).

invoices.ship_to_address / ship_to_state / ship_to_pincode record where a store order was delivered
when that differs from the retailer's registered address, frozen at issuance like the buyer_*
columns. All NULL for salesman orders and for every invoice issued before this migration, so those
invoices keep rendering to exactly the bytes their pdf_sha256 was taken from (research Pitfall 13).
Adding nullable columns is compatible with the migration-0045 immutability trigger, which guards
UPDATE and DELETE of rows, not DDL.
"""

from alembic import op
import sqlalchemy as sa

revision = "0065_invoice_ship_to"
down_revision = "0064_order_ship_to"
branch_labels = None
depends_on = None


def upgrade():
    # Imported inside the function so read-only alembic commands (heads, history) keep working
    # without env.py's sys.path setup (Phase 01 convention).
    from app.models.enums import INDIAN_STATES

    op.add_column("invoices", sa.Column("ship_to_address", sa.String(), nullable=True))
    op.add_column("invoices", sa.Column("ship_to_state", sa.String(), nullable=True))
    op.add_column("invoices", sa.Column("ship_to_pincode", sa.String(), nullable=True))
    states_list = ", ".join(f"'{s}'" for s in INDIAN_STATES)
    op.create_check_constraint(
        "ck_invoices_ship_to_state",
        "invoices",
        f"ship_to_state IS NULL OR ship_to_state IN ({states_list})",
    )


def downgrade():
    op.drop_constraint("ck_invoices_ship_to_state", "invoices", type_="check")
    op.drop_column("invoices", "ship_to_pincode")
    op.drop_column("invoices", "ship_to_state")
    op.drop_column("invoices", "ship_to_address")
