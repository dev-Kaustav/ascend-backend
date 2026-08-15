"""credit note invoice reference columns

Revision ID: 0048_credit_note_invoice_link
Revises: 0047_invoice_pdf_sha256
Create Date: 2026-08-15

Plan 02-05 (INV-06): a credit note must reference the invoice it reverses by number and
by date, in the shape GSTR-1's CDNR table requires. Adds a structural foreign key
(invoice_id, nullable — an order can reach DELIVERED with no invoice via direct-status
test fixtures or imported rows with no bill number) plus the flat CDNR literals
(original_invoice_number, original_invoice_date) that must survive independently of the
join since a credit note's filing reference cannot depend on a live lookup. note_type
distinguishes credit notes from debit notes (only 'C' is produced this phase); note_date
is the document date, distinct from created_at.
"""

from alembic import op
import sqlalchemy as sa

revision = "0048_credit_note_invoice_link"
down_revision = "0047_invoice_pdf_sha256"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("credit_notes", sa.Column("invoice_id", sa.Integer(), nullable=True))
    op.add_column("credit_notes", sa.Column("original_invoice_number", sa.String(), nullable=True))
    op.add_column("credit_notes", sa.Column("original_invoice_date", sa.DateTime(timezone=True), nullable=True))
    op.add_column("credit_notes", sa.Column("note_type", sa.String(), nullable=True))
    op.add_column("credit_notes", sa.Column("note_date", sa.DateTime(timezone=True), nullable=True))

    # Backfill existing rows before the NOT NULL constraint is applied to note_type.
    # credit_notes has zero rows in the dev database today, but seed_dummy.py can
    # repopulate it, so the backfill must be correct regardless.
    op.execute("UPDATE credit_notes SET note_type = 'C' WHERE note_type IS NULL")
    op.execute("UPDATE credit_notes SET note_date = created_at WHERE note_date IS NULL")

    op.alter_column("credit_notes", "note_type", nullable=False)

    op.create_index("ix_credit_notes_invoice_id", "credit_notes", ["invoice_id"])
    op.create_foreign_key(
        "fk_credit_notes_invoice_id_invoices",
        "credit_notes",
        "invoices",
        ["invoice_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_credit_notes_note_type",
        "credit_notes",
        "note_type IN ('C', 'D')",
    )


def downgrade():
    op.drop_constraint("ck_credit_notes_note_type", "credit_notes", type_="check")
    op.drop_constraint("fk_credit_notes_invoice_id_invoices", "credit_notes", type_="foreignkey")
    op.drop_index("ix_credit_notes_invoice_id", table_name="credit_notes")
    op.drop_column("credit_notes", "note_date")
    op.drop_column("credit_notes", "note_type")
    op.drop_column("credit_notes", "original_invoice_date")
    op.drop_column("credit_notes", "original_invoice_number")
    op.drop_column("credit_notes", "invoice_id")
