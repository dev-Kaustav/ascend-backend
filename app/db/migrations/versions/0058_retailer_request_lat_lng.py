"""retailer request coordinates

Revision ID: 0058_retailer_request_lat_lng
Revises: 0057_invoice_brand_series
Create Date: 2026-09-21

A salesman standing outside a new shop is the only person who will ever be at that spot with
the shop in front of them. Capturing the coordinates then is the cheapest they will ever be;
recovering them later means sending someone back. So the request carries them from the moment
it is filed, and `approve_request` copies them onto the retailer along with the rest of
REQUEST_FIELDS.

Nullable, deliberately. A request filed from a desk, or from a phone whose owner declined the
location permission, is still a valid request — the coordinates are a bonus, not a gate. The
outlet-finder already backfills a missing `retailers.latitude/longitude` on the first delivery
(see `mark_delivered`), so an approved retailer with no coordinates is a state the system
already knows how to leave.

Float matches `retailers.latitude/longitude` from 0037 exactly. Anything wider would have to
be narrowed on approve, and anything narrower would lose precision the phone already gave us.
"""

from alembic import op
import sqlalchemy as sa

revision = "0058_retailer_request_lat_lng"
down_revision = "0057_invoice_brand_series"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("retailer_requests", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("retailer_requests", sa.Column("longitude", sa.Float(), nullable=True))


def downgrade():
    op.drop_column("retailer_requests", "longitude")
    op.drop_column("retailer_requests", "latitude")
