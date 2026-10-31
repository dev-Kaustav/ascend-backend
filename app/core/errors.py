"""App-wide error responses.

Two failure classes used to reach the browser as a bare "Failed to fetch":

* A database constraint violation (a duplicate SKU code, an unknown brand id) that the service did
  not anticipate became an unhandled 500.
* Any unhandled 500 is answered by Starlette's outermost ServerErrorMiddleware, which sits outside
  CORSMiddleware. The response carries no Access-Control-Allow-Origin header, so the browser hides
  it from the page and fetch() rejects with a network error.

Constraint violations become a 409/400 with a sentence naming the field. Everything else is
caught inside CORS, logged with its traceback, and answered as a JSON 500 the page can read.
"""

import logging
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger("ascend.errors")

UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"

# Postgres: Key (code)=(447) already exists.  /  Key (brand_id)=(0) is not present in table "brands".
_PG_KEY = re.compile(r"Key \((?P<field>[^)]+)\)=\((?P<value>.*)\)")
# SQLite (the test harness): UNIQUE constraint failed: skus.code
_SQLITE_UNIQUE = re.compile(r"UNIQUE constraint failed: \w+\.(?P<field>\w+)")


def integrity_error_message(exc: IntegrityError) -> tuple[int, str]:
    orig = exc.orig
    pgcode = getattr(orig, "pgcode", None)
    detail = getattr(getattr(orig, "diag", None), "message_detail", None) or str(orig)
    key = _PG_KEY.search(detail) or _SQLITE_UNIQUE.search(detail)
    field = key.group("field").replace("_", " ") if key else None
    value = key.groupdict().get("value") if key else None
    shown = f"{field} '{value}'" if value else field

    if pgcode == UNIQUE_VIOLATION or (pgcode is None and _SQLITE_UNIQUE.search(detail)):
        return 409, f"{shown} is already used by another record." if field else "This record already exists."
    if pgcode == FOREIGN_KEY_VIOLATION:
        return 400, f"{shown} does not exist." if field else "A linked record does not exist."
    return 409, "This change conflicts with existing data."


def install_error_handlers(app: FastAPI) -> None:
    """Call before CORSMiddleware is added, so the catch-all below runs inside it."""

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError):
        status, message = integrity_error_message(exc)
        return JSONResponse(status_code=status, content={"detail": message})

    @app.middleware("http")
    async def _unhandled(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception:
            logger.exception("Unhandled error on %s %s", request.method, request.url.path)
            return JSONResponse(
                status_code=500,
                content={"detail": "Server error. Nothing was saved; try again, and report it if it repeats."},
            )
