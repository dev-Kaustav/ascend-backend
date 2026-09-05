"""retailer requests

Revision ID: 0055_retailer_requests
Revises: 0054_payment_details_and_qr
Create Date: 2026-09-04

Salesmen previously had no route to create an outlet at all; a short-lived change let them
create retailers directly, which is a fraud vector — a salesman could invent a shop and book
orders against it with nothing in between. That direct path is removed and replaced by this
table: a salesman files a proposal, an admin approves it, and only then does a `retailers` row
exist.

The retailer fields are copied onto the request rather than referenced. There is deliberately
no retailer row to point at while a request is pending, because a pending outlet must not be
orderable — `order_form_lookups` scopes a salesman's picker to real assigned retailers, so an
unapproved proposal is invisible there for free.

`mobile_number` is BigInteger, matching `retailers.mobile_number`. A 10-digit Indian mobile
is roughly 9.8e9 and does not fit PostgreSQL's 4-byte integer; SQLite accepts it regardless,
so this is a difference the SQLite suite cannot see. `pincode` stays a plain integer — six
digits fit comfortably.

`created_retailer_id` is the audit link from an approved request to the row it produced. It is
nullable because rejected, withdrawn and pending requests never produce one.

Index and constraint names are duplicated from `app/models/retailer_request.py` exactly and
must stay in step — a name drifting between the model and this revision would make a future
autogenerate try to drop and recreate it, the drift trap documented in 0050 and 0049.
"""

from alembic import op
import sqlalchemy as sa

revision = "0055_retailer_requests"
down_revision = "0054_payment_details_and_qr"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "retailer_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requested_by_employee_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("mobile_number", sa.BigInteger(), nullable=True),
        sa.Column("address_line1", sa.String(), nullable=True),
        sa.Column("address_line2", sa.String(), nullable=True),
        sa.Column("city", sa.String(), nullable=True),
        sa.Column("state", sa.String(), nullable=True),
        sa.Column("pincode", sa.Integer(), nullable=True),
        sa.Column("gst_number", sa.String(), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(), nullable=True),
        sa.Column("created_retailer_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["requested_by_employee_id"], ["employees.id"], name="fk_retailer_requests_requested_by_employees"
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"], ["users.id"], name="fk_retailer_requests_reviewed_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["created_retailer_id"], ["retailers.id"], name="fk_retailer_requests_created_retailer_retailers"
        ),
    )
    op.create_index("ix_retailer_requests_id", "retailer_requests", ["id"])
    op.create_index(
        "ix_retailer_requests_status_requester", "retailer_requests", ["status", "requested_by_employee_id"]
    )


def downgrade():
    op.drop_index("ix_retailer_requests_status_requester", table_name="retailer_requests")
    op.drop_index("ix_retailer_requests_id", table_name="retailer_requests")
    op.drop_table("retailer_requests")
