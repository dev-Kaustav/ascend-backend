"""Storefront catalogue data: categories, SKU catalogue columns.

STORE-07: the store needs an image, a category and a pack size per SKU, all admin-editable.
`categories` is an admin-managed table (D-13) rather than an enum so an admin can add and
rename them; names are unique case-insensitively via a lower(name) expression index.

`skus.net_weight_g` (whole grams, > 0) is the only pack-size source (D-03). `pack_type`
(box | case | ladi) and `units_per_pack` are a dormant provision (D-02): they are nullable,
admin-writable, and nothing reads them for ordering yet. No description column is added
(D-34).

Revision ids stay <= 32 characters (alembic_version.version_num is VARCHAR(32)).
"""

from alembic import op
import sqlalchemy as sa

revision = "0060_storefront_catalogue"
down_revision = "0059_landing_leads"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("icon_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_categories_id"), "categories", ["id"])
    op.create_index("uq_categories_name_lower", "categories", [sa.text("lower(name)")], unique=True)

    op.add_column("skus", sa.Column("image_url", sa.String(length=2048), nullable=True))
    op.add_column("skus", sa.Column("category_id", sa.Integer(), nullable=True))
    op.add_column("skus", sa.Column("net_weight_g", sa.Integer(), nullable=True))
    op.add_column("skus", sa.Column("pack_type", sa.String(length=8), nullable=True))
    op.add_column("skus", sa.Column("units_per_pack", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_skus_category_id_categories",
        "skus",
        "categories",
        ["category_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_skus_category_id", "skus", ["category_id"])
    op.create_check_constraint(
        "ck_skus_pack_type", "skus", "pack_type IS NULL OR pack_type IN ('box','case','ladi')"
    )
    op.create_check_constraint(
        "ck_skus_units_per_pack_positive", "skus", "units_per_pack IS NULL OR units_per_pack > 0"
    )
    op.create_check_constraint(
        "ck_skus_net_weight_g_positive", "skus", "net_weight_g IS NULL OR net_weight_g > 0"
    )


def downgrade():
    op.drop_constraint("ck_skus_net_weight_g_positive", "skus", type_="check")
    op.drop_constraint("ck_skus_units_per_pack_positive", "skus", type_="check")
    op.drop_constraint("ck_skus_pack_type", "skus", type_="check")
    op.drop_index("ix_skus_category_id", table_name="skus")
    op.drop_constraint("fk_skus_category_id_categories", "skus", type_="foreignkey")
    op.drop_column("skus", "units_per_pack")
    op.drop_column("skus", "pack_type")
    op.drop_column("skus", "net_weight_g")
    op.drop_column("skus", "category_id")
    op.drop_column("skus", "image_url")

    op.drop_index("uq_categories_name_lower", table_name="categories")
    op.drop_index(op.f("ix_categories_id"), table_name="categories")
    op.drop_table("categories")
