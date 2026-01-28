"""add sku pricing/tax attributes"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0007_sku_attributes"
down_revision = "0006_groups"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("skus", sa.Column("hsn_code", sa.String(), nullable=True))
    op.add_column("skus", sa.Column("pack_quantity", sa.Float(), nullable=True))
    op.add_column("skus", sa.Column("mrp", sa.Float(), nullable=True))
    op.add_column("skus", sa.Column("discount_amount", sa.Float(), nullable=True, server_default="0"))
    op.add_column("skus", sa.Column("discount_percent", sa.Float(), nullable=True, server_default="0"))
    op.add_column("skus", sa.Column("rate", sa.Float(), nullable=True))
    op.add_column("skus", sa.Column("sgst_percent", sa.Float(), nullable=True))
    op.add_column("skus", sa.Column("sgst_amount", sa.Float(), nullable=True))
    op.add_column("skus", sa.Column("cgst_percent", sa.Float(), nullable=True))
    op.add_column("skus", sa.Column("cgst_amount", sa.Float(), nullable=True))
    op.add_column("skus", sa.Column("amount", sa.Float(), nullable=True))
    op.add_column("skus", sa.Column("basis_rate", sa.Float(), nullable=True))
    op.add_column("skus", sa.Column("margin_percent", sa.Float(), nullable=True, server_default="0"))


def downgrade():
    op.drop_column("skus", "margin_percent")
    op.drop_column("skus", "basis_rate")
    op.drop_column("skus", "amount")
    op.drop_column("skus", "cgst_amount")
    op.drop_column("skus", "cgst_percent")
    op.drop_column("skus", "sgst_amount")
    op.drop_column("skus", "sgst_percent")
    op.drop_column("skus", "rate")
    op.drop_column("skus", "discount_percent")
    op.drop_column("skus", "discount_amount")
    op.drop_column("skus", "mrp")
    op.drop_column("skus", "pack_quantity")
    op.drop_column("skus", "hsn_code")
