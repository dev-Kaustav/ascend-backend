"""
One-time CLI helper to create an admin (or other role) user without exposing a public API.

Usage:
  python scripts/create_admin_user.py --email admin@ascend.com --password "strongpass" --role ADMIN
"""

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from app.db.session import SessionLocal  # noqa: E402
from app.services.admin import create_user  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Create a user directly in the database.")
    parser.add_argument("--email", required=True, help="User email")
    parser.add_argument("--password", required=True, help="User password")
    parser.add_argument("--role", default="ADMIN", help="Role (default: ADMIN)")
    parser.add_argument("--employee-id", type=int, dest="employee_id", help="Optional employee id")
    parser.add_argument(
        "--group-id",
        type=int,
        dest="group_id",
        help="Optional group id to assign (role must match if provided)",
    )
    parser.add_argument(
        "--inactive",
        action="store_true",
        help="Create the user as inactive (default is active)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        user = create_user(
            db=db,
            email=args.email,
            password=args.password,
            role=args.role,
            employee_id=args.employee_id,
            is_active=not args.inactive,
            group_id=args.group_id,
        )
        print(f"Created user id={user.id} email={user.email} role={user.role} active={user.is_active}")
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
