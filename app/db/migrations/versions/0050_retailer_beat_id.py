"""retailer beat_id

Revision ID: 0050_retailer_beat_id
Revises: 0049_inventory_quantity_check_constraints
Create Date: 2026-08-21

RPT-01 / D2: `Retailer` has no route/beat membership at all today — `Beat` is `id`, `name`,
`warehouse_id` and carries no outlet membership, and the only things in the schema that
reference a beat are `Order.beat_id` and `BillLabel.beat` (a bare string). This revision adds
`retailers.beat_id` as a nullable FK to `beats.id`: one beat per retailer, not a many-to-many.

The 6,195 existing retailers are left NULL on purpose. There is no source of truth for which
outlet sits on which route today, and a guessed assignment would produce a planned set that
looks authoritative and is fiction — D2 forbids inventing beat assignments in this migration.
`beats` already holds 26 rows (14 on warehouse `City`, 12 on warehouse `DLF`), so the FK
target is populated; no assignment logic is required or attempted here.

The constraint name `fk_retailers_beat_id_beats` and the index name `ix_retailers_beat_id`
are duplicated from `app/models/retailer.py` on purpose and must match exactly — a name
drifting between the model and this migration would make a future autogenerate try to drop
and recreate them, the same drift trap Phase 4 documented for the inventory CHECK constraints
in 0049.
"""

from alembic import op
import sqlalchemy as sa

revision = "0050_retailer_beat_id"
down_revision = "0049_inventory_quantity_check_constraints"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("retailers", sa.Column("beat_id", sa.Integer(), nullable=True))
    op.create_index("ix_retailers_beat_id", "retailers", ["beat_id"])
    op.create_foreign_key(
        "fk_retailers_beat_id_beats", "retailers", "beats", ["beat_id"], ["id"]
    )


def downgrade():
    op.drop_constraint("fk_retailers_beat_id_beats", "retailers", type_="foreignkey")
    op.drop_index("ix_retailers_beat_id", table_name="retailers")
    op.drop_column("retailers", "beat_id")
