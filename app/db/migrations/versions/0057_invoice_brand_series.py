"""per-brand invoice series: ASC000001 -> ASC/JAB/0001

Each brand now runs its own consecutive serial, so the single global
`invoice_number_seq` can no longer express the numbering. This adds the per-series
counter table, records the series on each invoice, and replaces the global unique on
invoice_serial with a per-series one.

Invoices already issued keep their numbers untouched — they are immutable legal records
(D-01). They carry invoice_series NULL, so the new unique constraint does not see them.

Revision ID: 0057_invoice_brand_series
Revises: 0056_user_token_version
Create Date: 2026-09-17
"""

from alembic import op
import sqlalchemy as sa

revision = "0057_invoice_brand_series"
down_revision = "0056_user_token_version"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "invoice_number_counters",
        sa.Column("series_code", sa.String(), primary_key=True),
        sa.Column("last_serial", sa.Integer(), nullable=False, server_default="0"),
    )

    op.add_column("invoices", sa.Column("invoice_series", sa.String(), nullable=True))

    # The global unique is created unnamed by migration 0045's `unique=True`, so it
    # lands on PostgreSQL's default constraint name.
    op.drop_constraint("invoices_invoice_serial_key", "invoices", type_="unique")
    op.create_unique_constraint(
        "uq_invoices_series_serial", "invoices", ["invoice_series", "invoice_serial"]
    )

    # invoice_number_seq (migration 0045) is deliberately left in place: it is no longer
    # read by application code, but dropping a sequence that historical serials were
    # drawn from buys nothing and cannot be undone cleanly.


def downgrade():
    op.drop_constraint("uq_invoices_series_serial", "invoices", type_="unique")
    op.create_unique_constraint(
        "invoices_invoice_serial_key", "invoices", ["invoice_serial"]
    )
    op.drop_column("invoices", "invoice_series")
    op.drop_table("invoice_number_counters")
