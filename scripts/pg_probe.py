"""
Run one or more SQL statements against the dev PostgreSQL database (resolved via
app.tests.pg_utils.resolve_host_database_url) and print the first column of the first row
of each result, one per line. Used by migration verification and by later plans.

Usage:
  python scripts/pg_probe.py "select count(*) from invoices" "select count(*) from retailers"
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from sqlalchemy import text  # noqa: E402

from app.tests.pg_utils import pg_engine  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/pg_probe.py \"<sql>\" [\"<sql>\" ...]", file=sys.stderr)
        sys.exit(1)

    engine = pg_engine()
    with engine.connect() as conn:
        for statement in sys.argv[1:]:
            result = conn.execute(text(statement))
            row = result.fetchone()
            print(row[0] if row is not None else "")


if __name__ == "__main__":
    main()
