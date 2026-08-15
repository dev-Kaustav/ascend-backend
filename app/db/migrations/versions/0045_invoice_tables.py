"""invoice and invoice_line tables, invoice_number_seq, immutability triggers

Revision ID: 0045_invoice_tables
Revises: 0044_decimal_money_integer_quantity
Create Date: 2026-08-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0045_invoice_tables"
down_revision = "0044_decimal_money_integer_quantity"
branch_labels = None
depends_on = None


def upgrade():
    # Imported inside the function, not at module scope: read-only alembic commands
    # (e.g. `alembic heads`, `alembic history`) load this module to build the revision
    # map without first running env.py's sys.path setup, so a module-level import of
    # an `app.*` package fails with ModuleNotFoundError for those commands.
    from app.models.enums import InvoiceStatus, InvoiceType, SupplyType, state_check_constraint
    from app.models.invoice import _enum_check_constraint

    # 1. invoice_number_seq -- the only producer of internal invoice serials (D-04).
    # Raw SQL rather than sa.Sequence so it is unambiguously a real PostgreSQL object.
    op.execute("CREATE SEQUENCE invoice_number_seq START WITH 1 INCREMENT BY 1")

    # 2. invoices
    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("invoice_number", sa.String(), nullable=False, unique=True),
        sa.Column("invoice_serial", sa.Integer(), nullable=True, unique=True),
        sa.Column("invoice_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default=InvoiceStatus.ISSUED.value),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("orders.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("invoice_type", sa.String(), nullable=False, server_default=InvoiceType.B2B.value),
        sa.Column("supply_type", sa.String(), nullable=False, server_default=SupplyType.REGULAR.value),
        sa.Column("reverse_charge", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("place_of_supply", sa.String(), nullable=False),
        sa.Column("is_inter_state", sa.Boolean(), nullable=False),
        sa.Column("supplier_legal_name", sa.String(), nullable=False),
        sa.Column("supplier_gstin", sa.String(), nullable=True),
        sa.Column("supplier_state", sa.String(), nullable=True),
        sa.Column("supplier_address", sa.String(), nullable=True),
        sa.Column("supplier_pincode", sa.String(), nullable=True),
        sa.Column("buyer_name", sa.String(), nullable=False),
        sa.Column("buyer_gstin", sa.String(), nullable=True),
        sa.Column("buyer_state", sa.String(), nullable=True),
        sa.Column("buyer_address", sa.String(), nullable=True),
        sa.Column("buyer_pincode", sa.String(), nullable=True),
        sa.Column("taxable_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("discount_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("cgst_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("sgst_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("igst_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("cess_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_tax_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("grand_total", sa.Numeric(12, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        _enum_check_constraint("invoices", "status", InvoiceStatus),
        _enum_check_constraint("invoices", "invoice_type", InvoiceType),
        _enum_check_constraint("invoices", "supply_type", SupplyType),
        state_check_constraint("invoices", column="place_of_supply"),
        state_check_constraint("invoices", column="supplier_state"),
        state_check_constraint("invoices", column="buyer_state"),
    )
    op.create_index("ix_invoices_invoice_number", "invoices", ["invoice_number"])

    # 3. invoice_lines
    op.create_table(
        "invoice_lines",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "invoice_id", sa.Integer(), sa.ForeignKey("invoices.id"), nullable=False
        ),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column(
            "sku_id", sa.Integer(), sa.ForeignKey("skus.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("hsn_code", sa.String(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("uqc", sa.String(), nullable=False),
        sa.Column("unit_rate", sa.Numeric(12, 2), nullable=False),
        sa.Column("discount_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("taxable_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("cgst_rate", sa.Numeric(12, 2), nullable=False),
        sa.Column("cgst_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("sgst_rate", sa.Numeric(12, 2), nullable=False),
        sa.Column("sgst_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("igst_rate", sa.Numeric(12, 2), nullable=False),
        sa.Column("igst_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("cess_rate", sa.Numeric(12, 2), nullable=False),
        sa.Column("cess_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_tax_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("line_total", sa.Numeric(12, 2), nullable=False),
        sa.UniqueConstraint("invoice_id", "line_number", name="uq_invoice_lines_invoice_line"),
    )
    op.create_index("ix_invoice_lines_invoice_id", "invoice_lines", ["invoice_id"])

    # 4. Immutability trigger function + four triggers (D-06, defence in depth behind the
    # ORM guard in app/models/invoice.py).
    op.execute(
        """
        CREATE FUNCTION fn_invoice_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'issued invoice is immutable: % on table % is not permitted', TG_OP, TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoices_immutable_update
        BEFORE UPDATE ON invoices
        FOR EACH ROW EXECUTE FUNCTION fn_invoice_immutable();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoices_immutable_delete
        BEFORE DELETE ON invoices
        FOR EACH ROW EXECUTE FUNCTION fn_invoice_immutable();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_lines_immutable_update
        BEFORE UPDATE ON invoice_lines
        FOR EACH ROW EXECUTE FUNCTION fn_invoice_immutable();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_lines_immutable_delete
        BEFORE DELETE ON invoice_lines
        FOR EACH ROW EXECUTE FUNCTION fn_invoice_immutable();
        """
    )


def downgrade():
    op.execute("DROP TRIGGER trg_invoice_lines_immutable_delete ON invoice_lines")
    op.execute("DROP TRIGGER trg_invoice_lines_immutable_update ON invoice_lines")
    op.execute("DROP TRIGGER trg_invoices_immutable_delete ON invoices")
    op.execute("DROP TRIGGER trg_invoices_immutable_update ON invoices")
    op.execute("DROP FUNCTION fn_invoice_immutable()")

    op.drop_index("ix_invoice_lines_invoice_id", table_name="invoice_lines")
    op.drop_table("invoice_lines")

    op.drop_index("ix_invoices_invoice_number", table_name="invoices")
    op.drop_table("invoices")

    op.execute("DROP SEQUENCE invoice_number_seq")
