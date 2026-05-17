"""add latitude and longitude to retailers

Revision ID: 0037_retailer_lat_lng
Revises: 0036_beats
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa

revision = "0037_retailer_lat_lng"
down_revision = "0036_beats"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("retailers", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("retailers", sa.Column("longitude", sa.Float(), nullable=True))


def downgrade():
    op.drop_column("retailers", "longitude")
    op.drop_column("retailers", "latitude")
