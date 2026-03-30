"""add inventory view permission"""

from alembic import op
import sqlalchemy as sa

revision = "0023_add_inventory_view_permission"
down_revision = "0022_drop_sku_pack_quantity"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO permissions (code, description) VALUES (:c, :d) "
            "ON CONFLICT (code) DO NOTHING"
        ),
        {"c": "inventory.view", "d": "View inventory"},
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO role_permissions (role, permission_id)
            SELECT :role, p.id FROM permissions p WHERE p.code = :code
            ON CONFLICT (role, permission_id) DO NOTHING
            """
        ),
        {"role": "WAREHOUSE_MANAGER", "code": "inventory.view"},
    )


def downgrade():
    conn = op.get_bind()
    perm_id = conn.execute(
        sa.text("SELECT id FROM permissions WHERE code = :code"),
        {"code": "inventory.view"},
    ).scalar()
    if not perm_id:
        return
    conn.execute(
        sa.text("DELETE FROM role_permissions WHERE permission_id = :pid"),
        {"pid": perm_id},
    )
    conn.execute(
        sa.text("DELETE FROM group_permissions WHERE permission_id = :pid"),
        {"pid": perm_id},
    )
    conn.execute(
        sa.text("DELETE FROM user_permissions WHERE permission_id = :pid"),
        {"pid": perm_id},
    )
    conn.execute(
        sa.text("DELETE FROM permissions WHERE id = :pid"),
        {"pid": perm_id},
    )
