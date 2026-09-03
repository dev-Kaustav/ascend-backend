"""Load the company profile (supplier identity, bank details, payment QR) into a database.

This exists because those values are configuration, not code, and they have to be applied
to a fresh database — including production — without hand-writing SQL. It is safe to run
against production: it is idempotent, it refuses to run without an explicit --apply, and
it never deletes anything.

Usage:

    # See what would change, touching nothing (the default):
    DATABASE_URL=postgresql://... python scripts/configure_company_profile.py \\
        --config scripts/company_profile.json --qr-image ../ascend-frontend/public/qr.png

    # Actually write:
    DATABASE_URL=postgresql://... python scripts/configure_company_profile.py \\
        --config scripts/company_profile.json --qr-image ../ascend-frontend/public/qr.png --apply

Run it from the ascend-backend directory (or set PYTHONPATH to it).

Why the QR is not simply re-uploaded every run: payment_qr_images is append-only and keyed
by content digest, so re-running with the same file resolves to the existing row instead of
inserting a duplicate. Invoices freeze the image they were issued with, so replacing the QR
never rewrites an already-issued document.
"""

import argparse
import io
import json
import mimetypes
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# The app's settings modules read these at import time, and SECRET_KEY is irrelevant to
# this script — it never issues a token. DATABASE_URL is not defaulted: writing to the
# wrong database is the one mistake this script must not make quietly.
os.environ.setdefault("SECRET_KEY", "unused-by-this-script")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import CompanyProfile, PaymentQRImage  # noqa: E402
from app.models.payment_qr_image import digest_image  # noqa: E402
from app.schemas.admin import CompanyProfileUpdate  # noqa: E402
from app.services.admin import (  # noqa: E402
    get_company_profile,
    set_payment_qr_image,
    update_company_profile,
)
from app.services.invoice import missing_company_invoice_fields  # noqa: E402

PROFILE_FIELDS = (
    "legal_name",
    "gstin",
    "address_line1",
    "address_line2",
    "city",
    "state",
    "pincode",
    "phone",
    "email",
    "invoice_prefix",
    "invoice_footer",
    "bank_name",
    "bank_account_name",
    "bank_account_number",
    "bank_ifsc",
    "bank_branch",
)


def _redact(field: str, value):
    """Bank account numbers are printed on invoices, but a terminal log is a different
    audience from a customer's invoice — show only the last 4 so a shared screen or a CI
    log does not carry the full number."""
    if field == "bank_account_number" and value:
        tail = str(value)[-4:]
        return f"****{tail}"
    return value


def load_config(path):
    with open(path) as f:
        config = json.load(f)
    unknown = set(config) - set(PROFILE_FIELDS)
    if unknown:
        raise SystemExit(
            f"Unknown field(s) in {path}: {', '.join(sorted(unknown))}.\n"
            f"Allowed: {', '.join(PROFILE_FIELDS)}"
        )
    return config


def diff_profile(profile, config):
    """Which fields would actually change. Empty means the database already matches."""
    changes = {}
    for field, desired in config.items():
        current = getattr(profile, field, None)
        # Treat "" and None as the same absent value, matching the API's own behaviour.
        if (current or None) != (desired or None):
            changes[field] = (current, desired)
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="JSON file of company profile values")
    parser.add_argument("--qr-image", help="Path to the payment QR image (PNG/JPEG/GIF)")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write. Without this the script only reports what would change.",
    )
    parser.add_argument("--database-url", help="Overrides the DATABASE_URL environment variable")
    args = parser.parse_args()

    database_url = args.database_url or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required (env var or --database-url).")

    config = load_config(args.config)

    qr_bytes = None
    qr_type = None
    if args.qr_image:
        with open(args.qr_image, "rb") as f:
            qr_bytes = f.read()
        # Sniff the real format instead of trusting the extension: a JPEG saved as
        # "qr.png" is common, and guessing from the filename would store a content_type
        # that contradicts the bytes.
        from PIL import Image as _PILImage

        with _PILImage.open(io.BytesIO(qr_bytes)) as probe:
            detected = (probe.format or "").upper()
        qr_type = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif"}.get(detected)
        if qr_type is None:
            raise SystemExit(f"{args.qr_image} is {detected or 'an unrecognised format'}; expected PNG, JPEG or GIF.")
        guessed = mimetypes.guess_type(args.qr_image)[0]
        if guessed and guessed != qr_type:
            print(f"note: {os.path.basename(args.qr_image)} is named like {guessed} but is actually {qr_type}.\n")

    # Show which database, with credentials stripped, so a misdirected run is obvious.
    safe_url = database_url
    if "@" in safe_url:
        scheme, rest = safe_url.split("://", 1)
        safe_url = f"{scheme}://***@{rest.split('@', 1)[1]}"
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"[{mode}] target: {safe_url}\n")

    engine = create_engine(database_url)
    db = sessionmaker(bind=engine)()
    try:
        profile = get_company_profile(db)
        changes = diff_profile(profile, config)

        if changes:
            print("Profile fields to change:")
            for field, (current, desired) in sorted(changes.items()):
                print(f"  {field}: {_redact(field, current)!r} -> {_redact(field, desired)!r}")
        else:
            print("Profile fields: already up to date, nothing to change.")

        qr_action = None
        if qr_bytes:
            digest = digest_image(qr_bytes)
            existing = db.query(PaymentQRImage).filter(PaymentQRImage.sha256 == digest).first()
            if existing and profile.payment_qr_image_id == existing.id:
                qr_action = f"QR: already set to this exact image (id={existing.id}), nothing to do."
            elif existing:
                qr_action = f"QR: repoint profile to existing image id={existing.id} (same content, already stored)."
            else:
                qr_action = f"QR: store new image ({len(qr_bytes):,} bytes, {qr_type}, sha256={digest[:12]}…) and point the profile at it."
            print("\n" + qr_action)

        if not args.apply:
            print("\nDry run — nothing was written. Re-run with --apply to commit.")
            return

        if changes:
            # Send the full field set, not just the diff: CompanyProfileUpdate replaces
            # the row, so omitting a field would null it out.
            merged = {field: getattr(profile, field, None) for field in PROFILE_FIELDS}
            merged.update(config)
            update_company_profile(db, CompanyProfileUpdate(**merged))
            print("\nProfile written.")

        if qr_bytes:
            set_payment_qr_image(db, qr_bytes, qr_type, label=os.path.basename(args.qr_image))
            print("QR written.")

        db.expire_all()
        final = get_company_profile(db)
        missing = missing_company_invoice_fields(final)
        print("\nResult:")
        print(f"  GSTIN               : {final.gstin}")
        print(f"  State               : {final.state}")
        print(f"  Bank                : {final.bank_name or '—'}")
        print(f"  Account             : {_redact('bank_account_number', final.bank_account_number) or '—'}")
        print(f"  IFSC                : {final.bank_ifsc or '—'}")
        print(f"  Payment QR image id : {final.payment_qr_image_id or '—'}")
        if missing:
            print(f"\n  WARNING: still missing for a valid tax invoice: {', '.join(missing)}")
        else:
            print("\n  Ready to issue tax invoices.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
