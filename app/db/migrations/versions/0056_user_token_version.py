"""user token version

Revision ID: 0056_user_token_version
Revises: 0055_retailer_requests
Create Date: 2026-09-10

Sessions are stateless JWTs: nothing server-side records which tokens exist, so a password
change could not end the sessions opened with the old password. Anyone still holding a valid
access or refresh token kept working until it expired — up to seven days for a refresh token.
That is the wrong behaviour for the case a password change usually means: the credential is
believed compromised, or the person holding it should no longer be inside.

`token_version` closes it without introducing session storage. Every token is minted carrying
the version current at login; `get_current_user` refuses a token whose version does not match
the user's row. Changing a password increments the column, which invalidates every token
issued before it in one write, on every device at once.

Server default "0" rather than a nullable column: the check compares integers on every
authenticated request, and a NULL would make that comparison a special case in the hot path.
Existing rows all become version 0, and tokens minted before this revision carry no version
claim and are read as 0 — so a deploy does not sign everybody out, only a password change does.
"""
from alembic import op
import sqlalchemy as sa

revision = "0056_user_token_version"
down_revision = "0055_retailer_requests"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("users", "token_version")
