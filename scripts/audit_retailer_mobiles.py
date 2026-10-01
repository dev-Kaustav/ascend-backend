"""Read-only audit of retailers.mobile_number, run before retailers start signing in by OTP.

Retailer login links a verified phone number to a retailer record only when exactly one
record carries that number (08-03, D-20). This script reports how clean that column is, so
the user can see how many existing shops will be recognised. It answers research Open
Question 5 against the real database. It only SELECTs, never commits, and prints at most the
last 4 digits of any number.

Usage:

    DATABASE_URL=postgresql://... python scripts/audit_retailer_mobiles.py

Run it from the ascend-backend directory (or set PYTHONPATH to it).
"""

import argparse
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# SECRET_KEY is irrelevant here (no token is issued). DATABASE_URL is not defaulted: pointing
# at the wrong database is the mistake this script must not make quietly.
os.environ.setdefault("SECRET_KEY", "unused-by-this-script")

from sqlalchemy import func  # noqa: E402

from app.models import Retailer, User  # noqa: E402

SAMPLE_LIMIT = 10

VALID_10 = (Retailer.mobile_number >= 6_000_000_000) & (Retailer.mobile_number <= 9_999_999_999)
PREFIXED_91 = (Retailer.mobile_number >= 910_000_000_000) & (Retailer.mobile_number <= 919_999_999_999)


def _count(session, *conditions) -> int:
    return session.query(func.count(Retailer.id)).filter(*conditions).scalar() or 0


def audit(session) -> dict:
    total = _count(session)
    null_mobile = _count(session, Retailer.mobile_number.is_(None))
    valid = _count(session, VALID_10)
    prefixed = _count(session, PREFIXED_91)
    shared = (
        session.query(Retailer.mobile_number, func.count(Retailer.id).label("n"))
        .filter(Retailer.mobile_number.isnot(None))
        .group_by(Retailer.mobile_number)
        .having(func.count(Retailer.id) > 1)
        .all()
    )
    linked_users = session.query(func.count(User.id)).filter(User.retailer_id.isnot(None)).scalar() or 0
    return {
        "total": total,
        "null_mobile": null_mobile,
        "valid_10_digit": valid,
        "prefixed_91_12_digit": prefixed,
        "other_invalid": total - null_mobile - valid - prefixed,
        "shared_numbers": len(shared),
        "retailers_on_shared_numbers": sum(row.n for row in shared),
        "already_linked_users": linked_users,
    }


def _masked_samples(session, *conditions) -> list[str]:
    rows = session.query(Retailer.mobile_number).filter(*conditions).order_by(Retailer.id).limit(SAMPLE_LIMIT).all()
    return ["****" + str(row[0])[-4:] for row in rows]


def format_report(session) -> str:
    result = audit(session)
    lines = ["Retailer mobile audit (read-only)"]
    lines += [f"  {key}: {value}" for key, value in result.items()]
    buckets = {
        "prefixed_91_12_digit": (PREFIXED_91,),
        "other_invalid": (
            Retailer.mobile_number.isnot(None),
            ~VALID_10,
            ~PREFIXED_91,
        ),
    }
    for name, conditions in buckets.items():
        samples = _masked_samples(session, *conditions)
        if samples:
            lines.append(f"  samples {name} (last 4 digits, max {SAMPLE_LIMIT}): {', '.join(samples)}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required (it is deliberately not defaulted).", file=sys.stderr)
        return 2

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    session = sessionmaker(bind=create_engine(database_url))()
    try:
        print(format_report(session))
    finally:
        session.rollback()
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
