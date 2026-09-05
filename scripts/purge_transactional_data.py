"""Delete every order and invoice, and the records that hang off them.

Written to clear test data out of a QA environment. It is destructive and irreversible:
there is no undo, and issued invoices are deliberately protected by a PostgreSQL trigger
that this script has to disable in order to proceed. Take a dump first.

    pg_dump "$DATABASE_URL" > backup-$(date +%F).sql

Usage:

    DATABASE_URL=postgresql://... python scripts/purge_transactional_data.py            # dry run
    DATABASE_URL=postgresql://... python scripts/purge_transactional_data.py --apply

What it deletes, children first, in one transaction:

    order_item_taxes, order_item_batches   (via order_items)
    invoice_lines, invoices                (must precede orders: FK is ON DELETE RESTRICT)
    credit_note_items, credit_notes
    accounts                               (payments)
    order_trails
    order_items
    orders

`accounts` and `credit_notes` are not optional collateral — their `order_id` is NOT NULL,
so they cannot outlive the orders they point at. This wipes payment and credit-note
history along with the orders.

What it never touches: retailers, users, employees, SKUs, brands, warehouses, beats,
company_profile, payment_qr_images. Masters and configuration survive.

Inventory is handled separately, because deleting orders leaves it inconsistent:
reservations are released by the *order* lifecycle, so removing the orders strands
`reserved_quantity` forever. See --inventory.
"""

import argparse
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

os.environ.setdefault("SECRET_KEY", "unused-by-this-script")

from sqlalchemy import create_engine, text  # noqa: E402

# Order matters: children before parents. invoices must precede orders because
# invoices.order_id is ON DELETE RESTRICT, and invoice rows are trigger-protected.
DELETE_ORDER = [
    "order_item_taxes",
    "order_item_batches",
    "invoice_lines",
    "invoices",
    "credit_note_items",
    "credit_notes",
    "accounts",
    "order_trails",
    "order_items",
    "orders",
]

# Emptied only with --inventory reset. Order matters here too: inventory_transactions and
# order_item_batches both reference sku_batches, so the batches can only go once those are gone.
# inventory is a leaf — nothing references it — so it can go last without ceremony.
INVENTORY_TABLES = ["inventory_transactions", "sku_batches", "inventory"]

PRESERVED = [
    "retailers", "users", "employees", "skus", "brands", "warehouses",
    "beats", "company_profile", "payment_qr_images",
]

# Stock that physically left the warehouse and must be added back for --inventory restore.
# Only OUT_FOR_DELIVERY and DELIVERED decremented physical stock: PENDING/READY_TO_SHIP
# only ever held a reservation, and CANCELLED already had its stock restored by the cancel
# path. Including those would push stock above where it started.
_DISPATCHED_BATCH_LINKS = """
    FROM order_item_batches oib
    JOIN order_items oi ON oi.id = oib.order_item_id
    JOIN orders o ON o.id = oi.order_id
    WHERE o.status IN ('OUT_FOR_DELIVERY', 'DELIVERED')
"""

IMMUTABILITY_TRIGGERS = [
    ("invoices", "trg_invoices_immutable_delete"),
    ("invoices", "trg_invoices_immutable_update"),
    ("invoice_lines", "trg_invoice_lines_immutable_delete"),
    ("invoice_lines", "trg_invoice_lines_immutable_update"),
]


def counts(conn, tables):
    out = {}
    for t in tables:
        try:
            out[t] = conn.execute(text(f"SELECT count(*) FROM {t}")).scalar()
        except Exception:
            out[t] = None  # table absent on this schema version
    return out


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete. Without this, only reports what would go.")
    parser.add_argument("--database-url", help="Overrides the DATABASE_URL environment variable")
    parser.add_argument(
        "--inventory",
        choices=["release", "restore", "reset", "leave"],
        default="release",
        help=(
            "release (default): zero reserved_quantity on inventory and sku_batches, leave "
            "physical stock alone — reservations are the only thing certainly wrong after "
            "deleting orders. "
            "restore: additionally add back the stock the deleted orders consumed, returning "
            "levels to what they were before the test orders ran. "
            "reset: delete every row from inventory_transactions, sku_batches and inventory, "
            "for a full reload from source data. "
            "leave: touch nothing, accepting stock permanently reserved by deleted orders."
        ),
    )
    parser.add_argument("--keep-invoice-sequence", action="store_true",
                        help="Do not restart invoice_number_seq. By default it restarts at 1, "
                             "since every invoice is being deleted and nothing remains to collide with.")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the typed confirmation prompt (for non-interactive use).")
    args = parser.parse_args()

    database_url = args.database_url or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required (env var or --database-url).")

    safe_url = database_url
    if "@" in safe_url:
        scheme, rest = safe_url.split("://", 1)
        safe_url = f"{scheme}://***@{rest.split('@', 1)[1]}"

    engine = create_engine(database_url)
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"[{mode}] target: {safe_url}\n")

    with engine.connect() as conn:
        before = counts(conn, DELETE_ORDER + INVENTORY_TABLES)
        preserved_before = counts(conn, PRESERVED)

    # Read before the delete: order_item_batches records how much each order actually
    # took, and the purge destroys it.
    with engine.connect() as conn:
        try:
            restorable = conn.execute(
                text("SELECT coalesce(sum(oib.quantity),0) " + _DISPATCHED_BATCH_LINKS)
            ).scalar() or 0
        except Exception:
            restorable = 0
        try:
            txn_order_caused = conn.execute(text(
                "SELECT count(*) FROM inventory_transactions WHERE transaction_type IN ('OUT','RETURN')"
            )).scalar() or 0
            txn_receipts = conn.execute(text(
                "SELECT count(*) FROM inventory_transactions WHERE transaction_type = 'IN'"
            )).scalar() or 0
        except Exception:
            txn_order_caused = txn_receipts = 0

    total = sum(v for v in (before[t] for t in DELETE_ORDER) if v)
    print("Rows to delete:")
    for t in DELETE_ORDER:
        n = before[t]
        print(f"  {t:22s} {'(no such table)' if n is None else format(n, ',')}")
    print(f"  {'TOTAL':22s} {total:,}")

    print(f"\nInventory mode: {args.inventory}")
    if args.inventory == "release":
        print("  reserved_quantity -> 0 on inventory and sku_batches; physical stock untouched")
        txn = before.get("inventory_transactions")
        if txn:
            print(f"  inventory_transactions ({txn:,} rows) are KEPT — they are the ledger of physical")
            print("    stock movement and have no FK to orders. They will still reference sku_batches,")
            print("    which blocks deleting those batches later. Use --inventory reset to clear them.")
    elif args.inventory == "restore":
        print("  reserved_quantity -> 0, AND stock consumed by dispatched orders added back")
        print(f"  units to add back: {restorable:,} (from order_item_batches on "
              f"OUT_FOR_DELIVERY/DELIVERED orders)")
        print(f"  inventory_transactions: {txn_order_caused:,} OUT/RETURN rows deleted "
              f"(order movements),")
        print(f"                          {txn_receipts:,} IN rows KEPT (stock receipts — "
              f"they record how")
        print("                          the stock arrived, and this mode keeps that stock)")
    elif args.inventory == "reset":
        print("  every row DELETED from:")
        for table in INVENTORY_TABLES:
            n = before.get(table)
            print(f"    {table:24s} {'(no such table)' if n is None else format(n, ',')}")
        print("  stock returns to not-existing, not to zero — for a full reload from source data")
    else:
        print("  nothing (stock stays reserved by orders that will no longer exist)")

    print("\nPreserved (never touched):")
    print("  " + ", ".join(f"{t}={preserved_before[t]:,}" for t in PRESERVED if preserved_before[t] is not None))

    if before.get("invoices"):
        print(f"\nNOTE: {before['invoices']:,} issued invoice(s) will be deleted. This requires "
              f"disabling the\n      immutability triggers from migration 0045. Invoices are legal "
              f"tax records;\n      only proceed if this data is genuinely test data.")

    if not args.apply:
        print("\nDry run — nothing was deleted. Re-run with --apply.")
        return

    if total == 0 and args.inventory == "leave":
        print("\nNothing to do.")
        return

    if not args.yes:
        print(f"\nThis permanently deletes {total:,} rows from {safe_url}.")
        if input("Type DELETE to proceed: ").strip() != "DELETE":
            raise SystemExit("Aborted.")

    # One transaction: either the whole purge lands or none of it does. A half-purged
    # database (invoices gone, orders left) would violate the FKs the app relies on.
    with engine.begin() as conn:
        for table, trigger in IMMUTABILITY_TRIGGERS:
            conn.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}"))
        try:
            if args.inventory == "restore":
                # Mirrors order._restore_dispatched_inventory. Runs BEFORE the delete
                # loop below: it reads order_item_batches, which the purge destroys.
                conn.execute(text("""
                    UPDATE sku_batches b SET remaining_quantity = b.remaining_quantity + t.qty
                    FROM (SELECT oib.batch_id AS batch_id, sum(oib.quantity) AS qty""" +
                    _DISPATCHED_BATCH_LINKS + """ GROUP BY oib.batch_id) t
                    WHERE b.id = t.batch_id
                """))
                conn.execute(text("""
                    UPDATE inventory inv SET total_quantity = inv.total_quantity + t.qty
                    FROM (SELECT oi.sku_id AS sku_id, sb.warehouse_id AS warehouse_id,
                                 sum(oib.quantity) AS qty
                          FROM order_item_batches oib
                          JOIN order_items oi ON oi.id = oib.order_item_id
                          JOIN orders o ON o.id = oi.order_id
                          JOIN sku_batches sb ON sb.id = oib.batch_id
                          WHERE o.status IN ('OUT_FOR_DELIVERY', 'DELIVERED')
                          GROUP BY oi.sku_id, sb.warehouse_id) t
                    WHERE inv.sku_id = t.sku_id AND inv.warehouse_id = t.warehouse_id
                """))
                # OUT (dispatch) and RETURN (cancel-restore, credit-note restock) are all
                # caused by orders that are about to disappear. IN rows are stock receipts
                # from admin.add_inventory_receipt — unrelated to orders, and the only
                # record of how the stock this mode preserves got here. Keep those.
                conn.execute(text(
                    "DELETE FROM inventory_transactions WHERE transaction_type IN ('OUT','RETURN')"
                ))

            deleted = {}
            for t in DELETE_ORDER:
                if before[t] is None:
                    continue
                deleted[t] = conn.execute(text(f"DELETE FROM {t}")).rowcount

            if args.inventory in ("release", "restore"):
                conn.execute(text("UPDATE inventory SET reserved_quantity = 0 WHERE reserved_quantity <> 0"))
                conn.execute(text("UPDATE sku_batches SET reserved_quantity = 0 WHERE reserved_quantity <> 0"))
            elif args.inventory == "reset":
                # Rows are deleted, not zeroed. Zeroing leaves a row per (sku, warehouse) and
                # per batch asserting "we hold zero of this", which is a claim about stock
                # rather than the absence of one — and a reload from source data then has to
                # reconcile against those ghosts. add_inventory_receipt recreates an inventory
                # row when none exists, so nothing needs them to survive.
                for table in INVENTORY_TABLES:
                    if before.get(table) is None:
                        continue
                    deleted[table] = conn.execute(text(f"DELETE FROM {table}")).rowcount

            if not args.keep_invoice_sequence:
                # Safe only because every invoice row is gone; invoice_number is UNIQUE, so
                # restarting against surviving invoices would collide on the next issuance.
                conn.execute(text("ALTER SEQUENCE invoice_number_seq RESTART WITH 1"))
        finally:
            # Re-enable inside the same transaction, so the protection is never left off
            # even if the delete raises and rolls back.
            for table, trigger in IMMUTABILITY_TRIGGERS:
                conn.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}"))

    print("\nDeleted:")
    for t, n in deleted.items():
        print(f"  {t:22s} {n:,}")
    if not args.keep_invoice_sequence:
        print("  invoice_number_seq restarted at 1")

    with engine.connect() as conn:
        # Include the stock tables when reset emptied them, so "remaining: 0" is a real check
        # on this run rather than a report about a different set of tables.
        verified = DELETE_ORDER + (INVENTORY_TABLES if args.inventory == "reset" else [])
        after = counts(conn, verified)
        preserved_after = counts(conn, PRESERVED)
        remaining = sum(v for v in after.values() if v)
        print(f"\nRemaining in purged tables: {remaining:,}")
        lost = [t for t in PRESERVED
                if preserved_before[t] is not None and preserved_before[t] != preserved_after[t]]
        if lost:
            print(f"  WARNING: preserved tables changed: {lost}")
        else:
            print("  Preserved tables unchanged.")


if __name__ == "__main__":
    main()
