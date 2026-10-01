"""Fill the Jabsons range's storefront catalogue data (image, category, pack size) from the
merchant feed, into SKUs that already exist.

The storefront (STORE-07) needs every SKU to carry an image, a category and a net weight in
grams. The Jabsons merchant feed already has the images and, in each row's code, the pack
size; this script applies them. It is safe to run against production: dry-run is the default,
it only fills fields that are still empty (so anything an admin set by hand is never
overwritten), it never creates a SKU, and it never deletes or truncates anything.

Usage:

    # See what would change, touching nothing (the default):
    DATABASE_URL=postgresql://... python scripts/seed_storefront_catalogue.py

    # Actually write:
    DATABASE_URL=postgresql://... python scripts/seed_storefront_catalogue.py --apply

    # Check every Jabsons SKU is complete (exit 1 if any lacks image, category or pack size):
    DATABASE_URL=postgresql://... python scripts/seed_storefront_catalogue.py --verify

Run it from the ascend-backend directory (or set PYTHONPATH to it). The feed lives at
scripts/jabsons_products.csv in this repo because a server checkout has no sibling
directory to read it from.

Rows are matched to SKUs by exact SKU.code (the feed's retailer_id, trimmed). A feed code with
no SKU is reported, not created: the report is how an operator learns whether the SKU rows
exist in a given database. Running --apply twice leaves the same state; the second run
reports no changes.
"""

import argparse
import csv
import os
import re
import sys
from dataclasses import dataclass, field

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# The app's settings modules read these at import time, and SECRET_KEY is irrelevant to
# this script — it never issues a token. DATABASE_URL is not defaulted: writing to the
# wrong database is the one mistake this script must not make quietly.
os.environ.setdefault("SECRET_KEY", "unused-by-this-script")

from sqlalchemy import create_engine, func  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.models import Brand, Category, SKU  # noqa: E402
from app.schemas.admin import validate_https_url  # noqa: E402

DEFAULT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jabsons_products.csv")

# Shopper aisles (D-12). Created only when a SKU needs one and no category of that name exists
# (case-insensitive), so a rename or reorder an admin made is respected.
CATEGORY_SEED = (
    ("Roasted Nuts & Seeds", 10),
    ("Namkeen & Bhujia", 20),
    ("Dry Fruits", 30),
)

# SKU-code prefix (the part before the first '-') -> aisle name (D-12). An unknown prefix is
# reported, never guessed.
CODE_PREFIX_CATEGORY = {
    "PNT": "Roasted Nuts & Seeds",
    "CHN": "Roasted Nuts & Seeds",
    "NAM": "Namkeen & Bhujia",
    "DF": "Dry Fruits",
}

JABSONS_BRAND_PREFIX = "jabsons"
CATALOGUE_FIELDS = ("image_url", "category_id", "net_weight_g")

_CODE_WEIGHT = re.compile(r"-(\d+)g$")
_TITLE_WEIGHT = re.compile(r"\b(\d+)g\b")


@dataclass
class SeedReport:
    created_categories: list = field(default_factory=list)
    # code -> list of fields filled (or that would be filled in a dry run)
    updated: dict = field(default_factory=dict)
    # feed codes with no SKU row
    unmatched_codes: list = field(default_factory=list)
    unknown_prefix_codes: list = field(default_factory=list)
    unparsed_weight_codes: list = field(default_factory=list)
    invalid_image_codes: list = field(default_factory=list)
    # code -> fields left alone because the SKU already holds a different value
    kept_existing: dict = field(default_factory=dict)

    @property
    def changes(self):
        return len(self.created_categories) + len(self.updated)


def load_rows(csv_path):
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def parse_net_weight_g(code, title):
    """Whole grams from the code suffix '-<n>g', else from '<n>g' as a word in the title."""
    for pattern, text in ((_CODE_WEIGHT, (code or "").strip()), (_TITLE_WEIGHT, title or "")):
        match = pattern.search(text)
        if match:
            grams = int(match.group(1))
            if grams > 0:
                return grams
    return None


def category_for_code(code):
    prefix = (code or "").strip().split("-", 1)[0].upper()
    return CODE_PREFIX_CATEGORY.get(prefix)


def _find_category(session, name):
    return session.query(Category).filter(func.lower(Category.name) == name.lower()).first()


def run(session, rows, *, apply):
    """Plan or apply the seed. Commits only when apply is True; otherwise rolls back, so a
    dry run reports exactly what an apply would do without leaving anything behind."""
    report = SeedReport()
    sort_orders = dict(CATEGORY_SEED)
    categories = {}  # aisle name -> Category row, resolved lazily

    def resolve_category(name):
        if name not in categories:
            category = _find_category(session, name)
            if category is None:
                category = Category(name=name, sort_order=sort_orders[name])
                session.add(category)
                session.flush()
                report.created_categories.append(name)
            categories[name] = category
        return categories[name]

    try:
        for row in rows:
            code = (row.get("retailer_id") or "").strip()
            if not code:
                continue
            sku = session.query(SKU).filter(SKU.code == code).first()
            if sku is None:
                report.unmatched_codes.append(code)
                continue

            filled = []
            kept = []

            # image_url
            image = None
            raw_image = (row.get("image_link") or "").strip()
            if raw_image:
                try:
                    image = validate_https_url(raw_image)
                except ValueError:
                    report.invalid_image_codes.append(code)
            else:
                report.invalid_image_codes.append(code)
            if image is not None:
                if sku.image_url is None:
                    sku.image_url = image
                    filled.append("image_url")
                elif sku.image_url != image:
                    kept.append("image_url")

            # category_id
            aisle = category_for_code(code)
            if aisle is None:
                report.unknown_prefix_codes.append(code)
            elif sku.category_id is None:
                sku.category_id = resolve_category(aisle).id
                filled.append("category_id")
            else:
                existing = session.get(Category, sku.category_id)
                if existing is None or existing.name.lower() != aisle.lower():
                    kept.append("category_id")

            # net_weight_g
            grams = parse_net_weight_g(code, row.get("title"))
            if grams is None:
                report.unparsed_weight_codes.append(code)
            elif sku.net_weight_g is None:
                sku.net_weight_g = grams
                filled.append("net_weight_g")
            elif sku.net_weight_g != grams:
                kept.append("net_weight_g")

            if filled:
                report.updated[code] = filled
            if kept:
                report.kept_existing[code] = kept

        if apply:
            session.commit()
        else:
            session.rollback()
    except Exception:
        session.rollback()
        raise
    return report


def verify(session):
    """Jabsons-brand SKUs still lacking an image, a category or a pack size (criterion 1).
    The range is identified by brand name (starts with 'Jabsons', case-insensitive)."""
    gaps = []
    skus = (
        session.query(SKU)
        .join(Brand, SKU.brand_id == Brand.id)
        .filter(func.lower(Brand.name).like(JABSONS_BRAND_PREFIX + "%"))
        .order_by(SKU.id)
        .all()
    )
    for sku in skus:
        missing = [name for name in CATALOGUE_FIELDS if getattr(sku, name) is None]
        if missing:
            gaps.append({"id": sku.id, "code": sku.code, "name": sku.name, "missing": missing})
    return gaps


def _print_report(report, *, apply):
    verb = "Filled" if apply else "Would fill"
    print(f"{verb} {len(report.updated)} SKU(s); categories {'created' if apply else 'to create'}: {len(report.created_categories)}")
    for name in report.created_categories:
        print(f"  category: {name}")
    for code, fields in report.updated.items():
        print(f"  {code}: {', '.join(fields)}")
    for label, codes in (
        ("Feed codes with no SKU (not created)", report.unmatched_codes),
        ("Unknown code prefix (category skipped)", report.unknown_prefix_codes),
        ("No pack size parsed (net_weight_g skipped)", report.unparsed_weight_codes),
        ("Invalid image link (image_url skipped)", report.invalid_image_codes),
    ):
        print(f"{label}: {len(codes)}")
        for code in codes:
            print(f"  {code}")
    print(f"Left as-is (already set to a different value): {len(report.kept_existing)}")
    for code, fields in report.kept_existing.items():
        print(f"  {code}: {', '.join(fields)}")


def _safe_url(database_url):
    if "@" in database_url and "://" in database_url:
        scheme, rest = database_url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return database_url


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", default=DEFAULT_CSV, help="Merchant feed CSV (default: jabsons_products.csv beside this script)")
    parser.add_argument("--apply", action="store_true", help="Actually write. Without this the script only reports what would change.")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Only check: list Jabsons SKUs lacking image, category or pack size; exit 1 if any. Never writes.",
    )
    parser.add_argument("--database-url", help="Overrides the DATABASE_URL environment variable")
    args = parser.parse_args(argv)
    if args.verify and args.apply:
        parser.error("--verify only checks; it cannot be combined with --apply.")

    database_url = args.database_url or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required (env var or --database-url).")

    if args.verify:
        print(f"[VERIFY] target: {_safe_url(database_url)}\n")
        engine = create_engine(database_url)
        session = sessionmaker(bind=engine)()
        try:
            gaps = verify(session)
        finally:
            session.close()
        if not gaps:
            print("All Jabsons SKUs have an image, a category and a pack size.")
            return 0
        print(f"{len(gaps)} Jabsons SKU(s) incomplete:")
        for gap in gaps:
            print(f"  {gap['code']} (id {gap['id']}, {gap['name']}): missing {', '.join(gap['missing'])}")
        return 1

    rows = load_rows(args.csv)
    print(f"[{'APPLY' if args.apply else 'DRY RUN'}] target: {_safe_url(database_url)}\n")
    engine = create_engine(database_url)
    session = sessionmaker(bind=engine)()
    try:
        report = run(session, rows, apply=args.apply)
        _print_report(report, apply=args.apply)
        if not args.apply:
            print("\nDry run — nothing was written. Re-run with --apply to commit.")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
