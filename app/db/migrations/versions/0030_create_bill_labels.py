"""compatibility stub for removed bill_labels creation revision

The `bill_labels` table is already created by `0028_workspace_access_rules`.
This revision is retained as a no-op so environments stamped at this revision
continue to migrate successfully.
"""

from alembic import op

revision = "0030_create_bill_labels"
down_revision = "0029_remove_legacy_access_rules"
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
