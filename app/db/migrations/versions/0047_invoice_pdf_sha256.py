"""add invoices.pdf_sha256

Revision ID: 0047_invoice_pdf_sha256
Revises: 0046_drop_order_invoice_number
Create Date: 2026-08-15

Plan 02-04 (INV-03, INV-08): the digest of the PDF rendered at issue time. Nullable,
because a NOT NULL here would couple invoice creation to PDF rendering succeeding, and
because invoices from a future backfill path may legitimately have no digest. Nothing
reads the stored PDF bytes themselves — only the digest is kept, which is the minimum
evidence that a regeneration reproduces the issue-time render.
"""

from alembic import op
import sqlalchemy as sa

revision = "0047_invoice_pdf_sha256"
down_revision = "0046_drop_order_invoice_number"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("invoices", sa.Column("pdf_sha256", sa.String(length=64), nullable=True))


def downgrade():
    op.drop_column("invoices", "pdf_sha256")
