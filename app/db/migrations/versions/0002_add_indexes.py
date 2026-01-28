"""add indexes

Revision ID: 0002_add_indexes
Revises: 0001_initial
Create Date: 2026-01-14 00:00:00.000000
"""

from alembic import op

revision = "0002_add_indexes"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_orders_created_at", "orders", ["created_at"])
    op.create_index("ix_orders_order_type", "orders", ["order_type"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_payment_status", "orders", ["payment_status"])
    op.create_index("ix_orders_from_entity_id", "orders", ["from_entity_id"])
    op.create_index("ix_orders_to_entity_id", "orders", ["to_entity_id"])
    op.create_index("ix_orders_salesman_id", "orders", ["salesman_id"])

    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])
    op.create_index("ix_order_items_sku_id", "order_items", ["sku_id"])

    op.create_index("ix_accounts_order_id", "accounts", ["order_id"])
    op.create_index("ix_accounts_created_at", "accounts", ["created_at"])

    op.create_index("ix_credit_notes_order_id", "credit_notes", ["order_id"])
    op.create_index("ix_credit_notes_created_at", "credit_notes", ["created_at"])

    op.create_index("ix_inventory_txn_sku_id", "inventory_transactions", ["sku_id"])
    op.create_index("ix_inventory_txn_warehouse_id", "inventory_transactions", ["warehouse_id"])
    op.create_index("ix_inventory_txn_created_at", "inventory_transactions", ["created_at"])

    op.create_index("ix_retailers_assigned_salesman_id", "retailers", ["assigned_salesman_id"])
    op.create_index("ix_employees_role", "employees", ["role"])
    op.create_index("ix_employees_warehouse_id", "employees", ["warehouse_id"])


def downgrade() -> None:
    op.drop_index("ix_employees_warehouse_id", table_name="employees")
    op.drop_index("ix_employees_role", table_name="employees")
    op.drop_index("ix_retailers_assigned_salesman_id", table_name="retailers")

    op.drop_index("ix_inventory_txn_created_at", table_name="inventory_transactions")
    op.drop_index("ix_inventory_txn_warehouse_id", table_name="inventory_transactions")
    op.drop_index("ix_inventory_txn_sku_id", table_name="inventory_transactions")

    op.drop_index("ix_credit_notes_created_at", table_name="credit_notes")
    op.drop_index("ix_credit_notes_order_id", table_name="credit_notes")

    op.drop_index("ix_accounts_created_at", table_name="accounts")
    op.drop_index("ix_accounts_order_id", table_name="accounts")

    op.drop_index("ix_order_items_sku_id", table_name="order_items")
    op.drop_index("ix_order_items_order_id", table_name="order_items")

    op.drop_index("ix_orders_salesman_id", table_name="orders")
    op.drop_index("ix_orders_to_entity_id", table_name="orders")
    op.drop_index("ix_orders_from_entity_id", table_name="orders")
    op.drop_index("ix_orders_payment_status", table_name="orders")
    op.drop_index("ix_orders_status", table_name="orders")
    op.drop_index("ix_orders_order_type", table_name="orders")
    op.drop_index("ix_orders_created_at", table_name="orders")
