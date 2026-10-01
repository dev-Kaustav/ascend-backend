"""Retailer phone-OTP login: users.phone_number and users.shop_confirmed_at.

Retailers sign in with a Firebase-verified mobile number (08-03, D-17..D-20), so the user
row needs the number as a lookup key. A nullable unique phone column was chosen over making
users.email nullable: UserAdminResponse.email is `str` and list_users returns retailer users
too, so a NULL email would break the admin user list the first time it is opened (research
Pitfall 7). OTP users instead get a synthetic non-routable email and an unusable password.

phone_number is BigInteger to match retailers.mobile_number; a 10-digit Indian mobile
overflows PostgreSQL's 4-byte integer. shop_confirmed_at stays NULL until the retailer
confirms their shop once.
"""

from alembic import op
import sqlalchemy as sa

revision = "0061_retailer_phone_login"
down_revision = "0060_storefront_catalogue"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("phone_number", sa.BigInteger(), nullable=True))
    op.add_column("users", sa.Column("shop_confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_users_phone_number", "users", ["phone_number"])


def downgrade():
    op.drop_constraint("uq_users_phone_number", "users", type_="unique")
    op.drop_column("users", "shop_confirmed_at")
    op.drop_column("users", "phone_number")
