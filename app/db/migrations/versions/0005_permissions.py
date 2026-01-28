"""add permissions model"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005_permissions"
down_revision = "0004_driver_statuses"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_permissions_id"), "permissions", ["id"], unique=False)
    op.create_unique_constraint("uq_permissions_code", "permissions", ["code"])

    op.create_table(
        "role_permissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("role", "permission_id", name="uq_role_permission"),
    )
    op.create_index(op.f("ix_role_permissions_id"), "role_permissions", ["id"], unique=False)

    op.create_table(
        "user_permissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.Column("is_allowed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "permission_id", name="uq_user_permission"),
    )
    op.create_index(op.f("ix_user_permissions_id"), "user_permissions", ["id"], unique=False)

    # Seed baseline permissions and assign to roles
    permissions = [
        ("orders.create", "Create orders"),
        ("orders.update", "Update orders"),
        ("orders.view", "View orders"),
        ("inventory.manage", "Manage inventory"),
        ("accounting.view", "View accounting data"),
        ("users.manage", "Manage users and permissions"),
    ]
    conn = op.get_bind()
    for code, desc in permissions:
        conn.execute(sa.text("INSERT INTO permissions (code, description) VALUES (:c, :d) ON CONFLICT (code) DO NOTHING"), {"c": code, "d": desc})

    role_map = {
        "ADMIN": ["orders.create", "orders.update", "orders.view", "inventory.manage", "accounting.view", "users.manage"],
        "ACCOUNTANT": ["orders.view", "accounting.view"],
        "WAREHOUSE_MANAGER": ["orders.view", "inventory.manage"],
        "SALESMAN": ["orders.view"],
        "DRIVER": [],
        "RETAILER": [],
        "BRAND": [],
    }
    for role, codes in role_map.items():
        for code in codes:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO role_permissions (role, permission_id)
                    SELECT :role, p.id FROM permissions p WHERE p.code = :code
                    ON CONFLICT (role, permission_id) DO NOTHING
                    """
                ),
                {"role": role, "code": code},
            )


def downgrade():
    op.drop_index(op.f("ix_user_permissions_id"), table_name="user_permissions")
    op.drop_table("user_permissions")
    op.drop_index(op.f("ix_role_permissions_id"), table_name="role_permissions")
    op.drop_table("role_permissions")
    op.drop_index("uq_permissions_code", table_name="permissions")
    op.drop_index(op.f("ix_permissions_id"), table_name="permissions")
    op.drop_table("permissions")
