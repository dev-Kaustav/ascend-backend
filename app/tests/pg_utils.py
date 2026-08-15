"""Shared PostgreSQL connection resolution for tests and scripts that must exercise real
PostgreSQL objects (sequences, triggers) that the default SQLite test engine cannot express.
"""

import os
import re

from sqlalchemy import create_engine

_ENV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))


def resolve_host_database_url() -> str:
    """Return a DATABASE_URL usable from the host (not from inside the app's Docker network).

    Priority: TEST_POSTGRES_URL env var if set, else DATABASE_URL parsed out of .env with
    the Docker-internal host rewritten to localhost.
    """
    override = os.getenv("TEST_POSTGRES_URL")
    if override:
        return override

    with open(_ENV_PATH) as f:
        for line in f:
            match = re.match(r"^DATABASE_URL=(.*)$", line.strip())
            if match:
                url = match.group(1).strip().strip('"').strip("'")
                return url.replace("host.docker.internal", "localhost")

    raise RuntimeError(f"DATABASE_URL not found in {_ENV_PATH}")


def pg_engine():
    return create_engine(resolve_host_database_url())
