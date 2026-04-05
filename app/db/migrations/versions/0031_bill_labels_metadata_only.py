"""compatibility stub for removed metadata-only bill labels revision

The metadata-only bill label schema is already represented by
`0028_workspace_access_rules`. This revision remains in the chain so databases
stamped at `0031_bill_labels_metadata_only` can be recognized by Alembic.
"""

from alembic import op

revision = "0031_bill_labels_metadata_only"
down_revision = "0030_create_bill_labels"
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
