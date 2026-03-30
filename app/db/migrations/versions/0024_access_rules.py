"""add access rules table"""

from alembic import op
import sqlalchemy as sa

revision = "0024_access_rules"
down_revision = "0023_add_inventory_view_permission"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "access_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=True),
        sa.Column("permissions", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("path", name="uq_access_rules_path"),
    )
    op.create_index(op.f("ix_access_rules_id"), "access_rules", ["id"], unique=False)
    op.create_index(op.f("ix_access_rules_path"), "access_rules", ["path"], unique=False)

    access_rules = sa.table(
        "access_rules",
        sa.column("path", sa.String),
        sa.column("roles", sa.JSON),
        sa.column("permissions", sa.JSON),
    )

    rules = [
        {"path": "/app/orders/new", "roles": ["ADMIN", "ACCOUNTANT"], "permissions": []},
        {"path": "/app/orders/:orderId", "roles": ["ADMIN"], "permissions": []},
        {"path": "/app/inventory/add", "roles": ["ADMIN", "WAREHOUSE_MANAGER"], "permissions": ["inventory.manage"]},
        {"path": "/app/inventory", "roles": ["ADMIN", "WAREHOUSE_MANAGER"], "permissions": ["inventory.view"]},
        {"path": "/app/dashboard", "roles": ["ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER", "BRAND", "RETAILER"], "permissions": []},
        {"path": "/app/orders", "roles": ["ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER"], "permissions": []},
        {"path": "/app/accounting", "roles": ["ADMIN", "ACCOUNTANT"], "permissions": []},
        {"path": "/app/users", "roles": ["ADMIN"], "permissions": []},
        {"path": "/app/catalog", "roles": ["ADMIN"], "permissions": []},
        {"path": "/app/create", "roles": ["ADMIN"], "permissions": []},
        {"path": "/app/credit-notes", "roles": ["ADMIN"], "permissions": []},
        {"path": "/app/cash-collection", "roles": ["ADMIN"], "permissions": []},
        {"path": "/app/retail-coverage", "roles": ["ADMIN"], "permissions": []},
        {"path": "/app/salesmen", "roles": ["ADMIN"], "permissions": []},
        {"path": "/app/drivers", "roles": ["ADMIN", "WAREHOUSE_MANAGER"], "permissions": []},
        {"path": "/app/invoices", "roles": ["ADMIN"], "permissions": []},
        {"path": "/app/profile", "roles": ["ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER", "BRAND", "RETAILER"], "permissions": []},
    ]

    op.bulk_insert(access_rules, rules)


def downgrade():
    op.drop_index(op.f("ix_access_rules_path"), table_name="access_rules")
    op.drop_index(op.f("ix_access_rules_id"), table_name="access_rules")
    op.drop_table("access_rules")
