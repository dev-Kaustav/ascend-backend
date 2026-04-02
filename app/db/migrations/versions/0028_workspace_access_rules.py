"""add workspace access rules and bill labels"""

from alembic import op
import sqlalchemy as sa

revision = "0028_workspace_access_rules"
down_revision = "0027_drop_brand_contact_info"
branch_labels = None
depends_on = None


WORKSPACE_RULES = [
    {"path": "/app/orders/:orderId", "roles": ["ADMIN", "ACCOUNTANT", "WAREHOUSE_MANAGER"], "permissions": []},
    {"path": "/app/operations", "roles": ["ADMIN", "WAREHOUSE_MANAGER"], "permissions": []},
    {"path": "/app/finance", "roles": ["ADMIN", "ACCOUNTANT"], "permissions": []},
    {"path": "/app/master-data", "roles": ["ADMIN"], "permissions": []},
    {"path": "/app/admin", "roles": ["ADMIN"], "permissions": []},
]

LEGACY_PATHS = [
    "/app/inventory",
    "/app/inventory/add",
    "/app/accounting",
    "/app/users",
    "/app/brands",
    "/app/catalog",
    "/app/create",
    "/app/cash-collection",
    "/app/retail-coverage",
    "/app/salesmen",
    "/app/drivers",
    "/app/invoices",
    "/app/credit-notes",
]

RESTORE_RULES = [
    {"path": "/app/inventory", "roles": ["ADMIN", "WAREHOUSE_MANAGER"], "permissions": ["inventory.view"]},
    {"path": "/app/inventory/add", "roles": ["ADMIN", "WAREHOUSE_MANAGER"], "permissions": ["inventory.manage"]},
    {"path": "/app/accounting", "roles": ["ADMIN", "ACCOUNTANT"], "permissions": []},
    {"path": "/app/users", "roles": ["ADMIN"], "permissions": []},
    {"path": "/app/brands", "roles": ["ADMIN"], "permissions": []},
    {"path": "/app/catalog", "roles": ["ADMIN"], "permissions": []},
    {"path": "/app/create", "roles": ["ADMIN"], "permissions": []},
    {"path": "/app/cash-collection", "roles": ["ADMIN"], "permissions": []},
    {"path": "/app/retail-coverage", "roles": ["ADMIN"], "permissions": []},
    {"path": "/app/salesmen", "roles": ["ADMIN"], "permissions": []},
    {"path": "/app/drivers", "roles": ["ADMIN", "WAREHOUSE_MANAGER"], "permissions": []},
    {"path": "/app/invoices", "roles": ["ADMIN"], "permissions": []},
    {"path": "/app/credit-notes", "roles": ["ADMIN"], "permissions": []},
]

OPERATIONS_INVENTORY_RULE = {
    "path": "/app/operations/inventory/add",
    "roles": ["ADMIN", "WAREHOUSE_MANAGER"],
    "permissions": ["inventory.manage"],
}

ORIGINAL_ORDER_RULE = {
    "path": "/app/orders/:orderId",
    "roles": ["ADMIN"],
    "permissions": [],
}


def _access_rules_table():
    return sa.table(
        "access_rules",
        sa.column("path", sa.String()),
        sa.column("roles", sa.JSON()),
        sa.column("permissions", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def upsert_rule(connection, access_rules, path, roles, permissions):
    existing = connection.execute(
        sa.select(access_rules.c.path).where(access_rules.c.path == path)
    ).scalar_one_or_none()

    values = {
        "path": path,
        "roles": roles,
        "permissions": permissions,
    }

    if existing:
        connection.execute(
            sa.update(access_rules)
            .where(access_rules.c.path == path)
            .values(**values, updated_at=sa.text("CURRENT_TIMESTAMP"))
        )
        return

    connection.execute(
        sa.insert(access_rules).values(
            **values,
            created_at=sa.text("CURRENT_TIMESTAMP"),
            updated_at=sa.text("CURRENT_TIMESTAMP"),
        )
    )


def upgrade():
    connection = op.get_bind()
    access_rules = _access_rules_table()

    for rule in WORKSPACE_RULES:
        upsert_rule(connection, access_rules, rule["path"], rule["roles"], rule["permissions"])

    connection.execute(
        sa.delete(access_rules).where(access_rules.c.path.in_(LEGACY_PATHS))
    )
    upsert_rule(
        connection,
        access_rules,
        OPERATIONS_INVENTORY_RULE["path"],
        OPERATIONS_INVENTORY_RULE["roles"],
        OPERATIONS_INVENTORY_RULE["permissions"],
    )

    op.create_table(
        "bill_labels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("bill_no", sa.String(), nullable=True),
        sa.Column("invoice_date", sa.String(), nullable=True),
        sa.Column("salesman_name", sa.String(), nullable=True),
        sa.Column("beat", sa.String(), nullable=True),
        sa.Column("gst_no", sa.String(), nullable=True),
        sa.Column("outlet_name", sa.String(), nullable=True),
        sa.Column("original_filename", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_bill_labels_user_id", "bill_labels", ["user_id"], unique=False)


def downgrade():
    connection = op.get_bind()
    access_rules = _access_rules_table()

    op.drop_index("ix_bill_labels_user_id", table_name="bill_labels")
    op.drop_table("bill_labels")

    connection.execute(
        sa.delete(access_rules).where(access_rules.c.path == OPERATIONS_INVENTORY_RULE["path"])
    )

    for rule in RESTORE_RULES:
        upsert_rule(connection, access_rules, rule["path"], rule["roles"], rule["permissions"])

    connection.execute(
        sa.delete(access_rules).where(
            access_rules.c.path.in_([
                "/app/operations",
                "/app/finance",
                "/app/master-data",
                "/app/admin",
            ])
        )
    )
    upsert_rule(
        connection,
        access_rules,
        ORIGINAL_ORDER_RULE["path"],
        ORIGINAL_ORDER_RULE["roles"],
        ORIGINAL_ORDER_RULE["permissions"],
    )
