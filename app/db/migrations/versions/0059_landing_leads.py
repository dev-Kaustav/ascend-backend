"""Inbox for enquiries filed from the public marketing site.

Separate from `retailer_requests` deliberately. That table's `requested_by_employee_id` is NOT
NULL because a request there becomes a real retailer on approval; an unauthenticated caller
must never be able to write into that queue. This table is an inbox an admin reads, and
nothing downstream consumes it.

`mobile_number` is BigInteger to match `retailers.mobile_number` — a 10-digit Indian mobile is
~9.8e9 and overflows PostgreSQL's 4-byte integer. It is NOT NULL here, unlike on the retailer
tables: a lead with no callback number is not a lead.
"""

from alembic import op
import sqlalchemy as sa

revision = "0059_landing_leads"
down_revision = "0058_retailer_request_lat_lng"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "landing_leads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lead_type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("mobile_number", sa.BigInteger(), nullable=False),
        sa.Column("pincode", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_landing_leads_id"), "landing_leads", ["id"])
    # The dedupe lookup on POST filters by mobile_number + lead_type + created_at.
    op.create_index(
        "ix_landing_leads_mobile_created",
        "landing_leads",
        ["mobile_number", "created_at"],
    )


def downgrade():
    op.drop_index("ix_landing_leads_mobile_created", table_name="landing_leads")
    op.drop_index(op.f("ix_landing_leads_id"), table_name="landing_leads")
    op.drop_table("landing_leads")
