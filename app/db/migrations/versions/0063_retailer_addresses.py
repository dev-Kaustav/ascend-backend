"""Saved delivery addresses for storefront retailers (08-08, D-29) and retailers.signup_source.

Decision (add-alongside, accepted debt): the Retailer row keeps its single registered address
as the billing identity ("Bill To", GST state) and stays the implicit delivery address for
every order that has no ship-to snapshot (all salesman orders). Store orders carry their own
snapshot (08-09); a NULL snapshot means "deliver to the registered address". Promoting the
addresses to every order would mean migrating all existing retailers into this table and
changing the salesman order form, driver flows and outlet finder.
"""

from alembic import op
import sqlalchemy as sa

revision = "0063_retailer_addresses"
down_revision = "0062_auth_attempts"
branch_labels = None
depends_on = None


def upgrade():
    # Imported inside the function so read-only alembic commands (heads, history) keep working
    # without env.py's sys.path setup (Phase 01 convention).
    from app.models.enums import INDIAN_STATES

    op.add_column("retailers", sa.Column("signup_source", sa.String(length=16), nullable=True))

    states_list = ", ".join(f"'{s}'" for s in INDIAN_STATES)
    op.create_table(
        "retailer_addresses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("retailer_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=40), nullable=True),
        sa.Column("line1", sa.String(length=200), nullable=False),
        sa.Column("line2", sa.String(length=200), nullable=True),
        sa.Column("landmark", sa.String(length=120), nullable=True),
        sa.Column("city", sa.String(length=80), nullable=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("pincode", sa.Integer(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["retailer_id"], ["retailers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            f"state IS NULL OR state IN ({states_list})", name="ck_retailer_addresses_state"
        ),
    )
    op.create_index("ix_retailer_addresses_retailer_id", "retailer_addresses", ["retailer_id"])


def downgrade():
    op.drop_index("ix_retailer_addresses_retailer_id", table_name="retailer_addresses")
    op.drop_table("retailer_addresses")
    op.drop_column("retailers", "signup_source")
