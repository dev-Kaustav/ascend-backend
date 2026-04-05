"""compatibility stub for removed legacy access rules revision

This revision previously existed in the migration chain and some databases are
already stamped with it. The effective schema/data changes now live in
`0028_workspace_access_rules`, so this revision is intentionally a no-op.
"""

from alembic import op

revision = "0029_remove_legacy_access_rules"
down_revision = "0028_workspace_access_rules"
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
