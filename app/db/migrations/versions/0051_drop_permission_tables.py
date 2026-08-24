"""drop obsolete permission tables

Revision ID: 0051_drop_permission_tables
Revises: 0050_retailer_beat_id
Create Date: 2026-08-24

This intentionally discards 7 permissions, 12 role grants, 17 group grants, 0 user overrides,
and 10 access rules. The 5 groups and 3 non-null users.group_id assignments are retained. The
downgrade recreates only the empty table structures; it never restores discarded authorization
data.
"""

from alembic import op
import sqlalchemy as sa


revision = "0051_drop_permission_tables"
down_revision = "0050_retailer_beat_id"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index(op.f("ix_role_permissions_id"), table_name="role_permissions")
    op.drop_table("role_permissions")
    op.drop_index(op.f("ix_group_permissions_id"), table_name="group_permissions")
    op.drop_table("group_permissions")
    op.drop_index(op.f("ix_user_permissions_id"), table_name="user_permissions")
    op.drop_table("user_permissions")
    op.drop_index(op.f("ix_access_rules_path"), table_name="access_rules")
    op.drop_index(op.f("ix_access_rules_id"), table_name="access_rules")
    op.drop_table("access_rules")
    op.drop_constraint("uq_permissions_code", "permissions", type_="unique")
    op.drop_index(op.f("ix_permissions_id"), table_name="permissions")
    op.drop_table("permissions")


def downgrade():
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
        sa.ForeignKeyConstraint(
            ["permission_id"], ["permissions.id"], name="role_permissions_permission_id_fkey"
        ),
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
        sa.ForeignKeyConstraint(
            ["permission_id"], ["permissions.id"], name="user_permissions_permission_id_fkey"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="user_permissions_user_id_fkey"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "permission_id", name="uq_user_permission"),
    )
    op.create_index(op.f("ix_user_permissions_id"), "user_permissions", ["id"], unique=False)

    op.create_table(
        "group_permissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.Column("is_allowed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], name="group_permissions_group_id_fkey"),
        sa.ForeignKeyConstraint(
            ["permission_id"], ["permissions.id"], name="group_permissions_permission_id_fkey"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "permission_id", name="uq_group_permission"),
    )
    op.create_index(op.f("ix_group_permissions_id"), "group_permissions", ["id"], unique=False)

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
