"""add groups and group permissions"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0006_groups"
down_revision = "0005_permissions"
branch_labels = None
depends_on = None


def upgrade():
    employee_role = postgresql.ENUM(
        "ADMIN",
        "SALESMAN",
        "ACCOUNTANT",
        "WAREHOUSE_MANAGER",
        "DRIVER",
        "RETAILER",
        "BRAND",
        name="employee_role",
        create_type=False,
    )

    op.create_table(
        "groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("role", employee_role, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_groups_name"),
    )
    op.create_index(op.f("ix_groups_id"), "groups", ["id"], unique=False)

    op.create_table(
        "group_permissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.Column("is_allowed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "permission_id", name="uq_group_permission"),
    )
    op.create_index(op.f("ix_group_permissions_id"), "group_permissions", ["id"], unique=False)

    op.add_column("users", sa.Column("group_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_users_group_id_groups",
        "users",
        "groups",
        ["group_id"],
        ["id"],
        ondelete="SET NULL",
    )

    conn = op.get_bind()
    default_groups = [
        ("Administrators", "ADMIN"),
        ("Accountants", "ACCOUNTANT"),
        ("Warehouse Managers", "WAREHOUSE_MANAGER"),
        ("Sales Team", "SALESMAN"),
        ("Drivers", "DRIVER"),
    ]
    group_ids = {}
    for name, role in default_groups:
        result = conn.execute(
            sa.text(
                """
                INSERT INTO groups (name, role)
                VALUES (:name, :role)
                ON CONFLICT (name) DO NOTHING
                RETURNING id
                """
            ),
            {"name": name, "role": role},
        )
        new_id = result.scalar()
        if not new_id:
            new_id = conn.execute(sa.text("SELECT id FROM groups WHERE name = :name"), {"name": name}).scalar()
        group_ids[role] = new_id

        conn.execute(
            sa.text(
                """
                INSERT INTO group_permissions (group_id, permission_id, is_allowed)
                SELECT :group_id, rp.permission_id, true
                FROM role_permissions rp
                WHERE rp.role = :role
                ON CONFLICT (group_id, permission_id) DO NOTHING
                """
            ),
            {"group_id": new_id, "role": role},
        )

    for role, group_id in group_ids.items():
        if group_id:
            conn.execute(
                sa.text(
                    """
                    UPDATE users
                    SET group_id = :group_id
                    WHERE role = :role AND group_id IS NULL
                    """
                ),
                {"group_id": group_id, "role": role},
            )


def downgrade():
    op.drop_constraint("fk_users_group_id_groups", "users", type_="foreignkey")
    op.drop_column("users", "group_id")
    op.drop_index(op.f("ix_group_permissions_id"), table_name="group_permissions")
    op.drop_table("group_permissions")
    op.drop_index(op.f("ix_groups_id"), table_name="groups")
    op.drop_table("groups")
