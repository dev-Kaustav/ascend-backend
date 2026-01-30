"""store phone and pincode as numeric"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0018_numeric_phone_pincode"
down_revision = "0017_add_employee_phone_number"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE employees SET phone_number = NULLIF(regexp_replace(phone_number, '\\\\D', '', 'g'), '') "
        "WHERE phone_number IS NOT NULL"
    )
    op.execute(
        "UPDATE retailers SET mobile_number = NULLIF(regexp_replace(mobile_number, '\\\\D', '', 'g'), '') "
        "WHERE mobile_number IS NOT NULL"
    )
    op.execute(
        "UPDATE retailers SET pincode = NULLIF(regexp_replace(pincode, '\\\\D', '', 'g'), '') "
        "WHERE pincode IS NOT NULL"
    )
    op.execute(
        "UPDATE warehouses SET pincode = NULLIF(regexp_replace(pincode, '\\\\D', '', 'g'), '') "
        "WHERE pincode IS NOT NULL"
    )
    op.alter_column(
        "employees",
        "phone_number",
        type_=sa.BigInteger(),
        postgresql_using="phone_number::bigint",
    )
    op.alter_column(
        "retailers",
        "mobile_number",
        type_=sa.BigInteger(),
        postgresql_using="mobile_number::bigint",
    )
    op.alter_column(
        "retailers",
        "pincode",
        type_=sa.Integer(),
        postgresql_using="pincode::integer",
    )
    op.alter_column(
        "warehouses",
        "pincode",
        type_=sa.Integer(),
        postgresql_using="pincode::integer",
    )


def downgrade():
    op.alter_column(
        "warehouses",
        "pincode",
        type_=sa.String(),
        postgresql_using="pincode::text",
    )
    op.alter_column(
        "retailers",
        "pincode",
        type_=sa.String(),
        postgresql_using="pincode::text",
    )
    op.alter_column(
        "retailers",
        "mobile_number",
        type_=sa.String(),
        postgresql_using="mobile_number::text",
    )
    op.alter_column(
        "employees",
        "phone_number",
        type_=sa.String(),
        postgresql_using="phone_number::text",
    )
