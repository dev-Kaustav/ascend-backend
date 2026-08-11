"""state check constraint (28 states + 8 union territories)

Revision ID: 0043_state_check_constraint
Revises: 0042_outlet_assignments
Create Date: 2026-08-11
"""

from alembic import op
import sqlalchemy as sa

revision = "0043_state_check_constraint"
down_revision = "0042_outlet_assignments"
branch_labels = None
depends_on = None

_TABLES = ("retailers", "warehouses", "company_profile")


def _normalize(value: str) -> str:
    return "".join(ch for ch in value.strip().lower() if ch.isalnum())


def upgrade():
    # Imported inside the function, not at module scope: read-only alembic commands
    # (e.g. `alembic heads`, `alembic history`) load this module to build the revision
    # map without first running env.py's sys.path setup, so a module-level import of
    # an `app.*` package fails with ModuleNotFoundError for those commands.
    from app.models.enums import INDIAN_STATES

    bind = op.get_bind()

    for table in _TABLES:
        # Step A — normalise. Blank strings are not canonical; NULL is allowed.
        bind.execute(sa.text(f"UPDATE {table} SET state = NULL WHERE btrim(state) = ''"))

        # Map existing free-text values onto the canonical spelling. This mirrors
        # _normalize_state() (app/services/order.py:73-74), applied once, at rest.
        for canonical in INDIAN_STATES:
            bind.execute(
                sa.text(
                    f"UPDATE {table} SET state = :canonical "
                    "WHERE lower(regexp_replace(state, '[^a-zA-Z0-9]', '', 'g')) = :normalized "
                    "AND state <> :canonical"
                ),
                {"canonical": canonical, "normalized": _normalize(canonical)},
            )

        # Step B — the loud guard. Abort if anything survives that cannot be mapped.
        stmt = sa.text(
            f"SELECT DISTINCT state FROM {table} WHERE state IS NOT NULL AND state NOT IN :states"
        ).bindparams(sa.bindparam("states", expanding=True))
        offending = [row[0] for row in bind.execute(stmt, {"states": list(INDIAN_STATES)})]
        if offending:
            raise RuntimeError(f"0043: cannot map states in {table}: {offending}")

    # Step C — add the CHECK constraints, matching state_check_constraint() exactly.
    states_list = ", ".join(f"'{s}'" for s in INDIAN_STATES)
    for table in _TABLES:
        op.create_check_constraint(
            f"ck_{table}_state",
            table,
            f"state IS NULL OR state IN ({states_list})",
        )


def downgrade():
    for table in _TABLES:
        op.drop_constraint(f"ck_{table}_state", table, type_="check")
