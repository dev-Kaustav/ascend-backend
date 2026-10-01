"""Failed-login ledger backing the auth rate limiter (PROD-04).

A table rather than in-memory counters: counters reset on every restart and deploy, and would
loosen N-fold if Phase 7 ever runs more than one worker. Rows are tiny, purged after 24 hours
whenever a failure is recorded, and `created_at` is naive UTC written by the service.
"""

from alembic import op
import sqlalchemy as sa

revision = "0062_auth_attempts"
down_revision = "0061_retailer_phone_login"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "auth_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_auth_attempts_scope_key_created",
        "auth_attempts",
        ["scope", "key", "created_at"],
    )


def downgrade():
    op.drop_index("ix_auth_attempts_scope_key_created", table_name="auth_attempts")
    op.drop_table("auth_attempts")
