"""add bank payment details and payment QR to company profile and invoice snapshot

Revision ID: 0054_payment_details_and_qr
Revises: 0053_sku_code
Create Date: 2026-09-03

Bank details and the payment QR are snapshotted onto each invoice, not read live at
render time. That is forced by the existing byte-identity contract: `invoices.pdf_sha256`
is the proof that a regenerated PDF reproduces the issue-time render, so anything the PDF
draws that could change later (a new bank account, a replaced QR) must be frozen on the
invoice row or every historical digest breaks the moment it changes.

The QR image bytes therefore live in their own append-only table rather than on
company_profile. Replacing the current QR inserts a new row and repoints the profile; the
old row stays because invoices issued against it still render from it. Storing the bytes
inline on every invoice would duplicate an identical image thousands of times, and storing
only the current image on company_profile would silently rewrite the payment instructions
on every already-issued invoice.

All columns are nullable: every existing company profile and invoice predates them.
"""

from alembic import op
import sqlalchemy as sa


revision = "0054_payment_details_and_qr"
down_revision = "0053_sku_code"
branch_labels = None
depends_on = None


# company_profile carries the current values; invoices carry the frozen copy under a
# supplier_ prefix, matching the existing supplier_gstin / supplier_address convention.
_BANK_COLUMNS = [
    "bank_name",
    "bank_account_name",
    "bank_account_number",
    "bank_ifsc",
    "bank_branch",
]


def upgrade():
    op.create_table(
        "payment_qr_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("image_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        # Deduplicates re-uploads of the same file and gives the invoice snapshot a
        # verifiable identity, mirroring invoices.pdf_sha256.
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    for column in _BANK_COLUMNS:
        op.add_column("company_profile", sa.Column(column, sa.String(), nullable=True))
        op.add_column("invoices", sa.Column(f"supplier_{column}", sa.String(), nullable=True))

    op.add_column("company_profile", sa.Column("payment_qr_image_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_company_profile_payment_qr_image_id",
        "company_profile",
        "payment_qr_images",
        ["payment_qr_image_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column("invoices", sa.Column("payment_qr_image_id", sa.Integer(), nullable=True))
    # RESTRICT, not SET NULL: an issued invoice's payment instructions are part of the
    # frozen record. Deleting an image an invoice still points at must fail loudly.
    op.create_foreign_key(
        "fk_invoices_payment_qr_image_id",
        "invoices",
        "payment_qr_images",
        ["payment_qr_image_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade():
    op.drop_constraint("fk_invoices_payment_qr_image_id", "invoices", type_="foreignkey")
    op.drop_column("invoices", "payment_qr_image_id")

    op.drop_constraint("fk_company_profile_payment_qr_image_id", "company_profile", type_="foreignkey")
    op.drop_column("company_profile", "payment_qr_image_id")

    for column in _BANK_COLUMNS:
        op.drop_column("invoices", f"supplier_{column}")
        op.drop_column("company_profile", column)

    op.drop_table("payment_qr_images")
