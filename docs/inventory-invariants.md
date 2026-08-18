# Inventory Column Invariants

**Requirement:** INVT-03
**Scope:** `app/models/inventory.py`, `app/models/sku_batch.py`, `app/models/order_item_batch.py`,
`app/models/inventory_transaction.py`
**Executable half:** `app/tests/test_inventory_invariants.py::assert_inventory_invariants`

This is a reference document a developer reads before touching an inventory quantity
column, not a narrative. It is the authority for what each column means — the docstrings
and comments on the models must match this wording exactly.

## 1. What each column means

| Column | Meaning | Moved by |
|---|---|---|
| `SKUBatch.quantity_received` | Units this batch arrived with. Never decremented. | receipt only (`admin.py:138`) |
| `SKUBatch.remaining_quantity` | Physical units of this batch on hand right now. This meaning does not change with order status. | dispatch `-` (`order.py:220`), restore `+` (`order.py:254`), receipt `+` (`admin.py:139`) |
| `SKUBatch.reserved_quantity` | Subset of `remaining_quantity` claimed by orders not yet dispatched. | reserve `+` (`order.py:183`), dispatch `-` (`order.py:219`), release `-` (`order.py:243`) |
| `Inventory.total_quantity` | Physical units on hand across all batches of this sku at this warehouse. Same physical concept as `remaining_quantity`, one grain up. | dispatch `-` (`order.py:222`), restore `+` (`order.py:260`), receipt `+` (`admin.py:151`), credit-note restock `+` (`accounting.py:179`) |
| `Inventory.reserved_quantity` | Subset of `total_quantity` claimed by orders not yet dispatched. A subset, not an addition. | reserve `+` (`order.py:184`), dispatch `-` (`order.py:221`), release `-` (`order.py:245`) |
| `OrderItemBatch.quantity` | Units of one batch allocated to one order line. | written at reserve (`order.py:180`), deleted by release (`order.py:246`), retained by restore (`order.py:252-268` — no delete) |
| `InventoryTransaction.quantity` | Magnitude of one movement, always positive; direction lives in `transaction_type`. | one row per `IN` / `OUT` / `RETURN` |

## 2. The invariants

- **(I1)** `0 <= SKUBatch.reserved_quantity <= SKUBatch.remaining_quantity` — **database-enforced**
  by `ck_sku_batches_reserved_quantity_non_negative` and `ck_sku_batches_reserved_not_over_remaining`
  (migration `0049`, plan 04-01).
- **(I2)** `0 <= Inventory.reserved_quantity <= Inventory.total_quantity` — **database-enforced**
  by `ck_inventory_reserved_quantity_non_negative` and `ck_inventory_reserved_not_over_total`
  (migration `0049`, plan 04-01).
- **(I3)** `Inventory.total_quantity == SUM(SKUBatch.remaining_quantity)` for the same
  `(sku_id, warehouse_id)`. **Test-enforced** — this spans rows in two tables, so no CHECK
  constraint can express it. Enforced by `assert_inventory_invariants(db)`.
- **(I4)** `Inventory.reserved_quantity == SUM(SKUBatch.reserved_quantity)` for the same
  `(sku_id, warehouse_id)`. **Test-enforced**, same status as I3.
- **(I5)** `SKUBatch.reserved_quantity == SUM(OrderItemBatch.quantity)` across order lines
  whose order is still `PENDING` or `READY_TO_SHIP`. **Test-enforced.** Dispatch zeroes the
  reservation but keeps the allocation row (`order.py:219` decrements `reserved_quantity`,
  nothing deletes the `OrderItemBatch`), and a cancelled order's retained rows are likewise
  excluded — the status filter is load-bearing, not a footnote.

## 3. Known deviation: credit-note restock

`create_credit_note(restock=True)` (`accounting.py:167-186`) increments
`Inventory.total_quantity` and creates **no `SKUBatch`**. This breaks I3: the inventory row
says stock exists that no batch can supply, because FEFO allocation (`order.py:155-168`) reads
only `sku_batches`. The consequence is concrete — an order can fail as out of stock while the
inventory screen shows stock, because the restocked units are invisible to allocation.

`restock` defaults to `False` (`schemas/accounting.py:64`) and **no code path in this codebase
sets it to `True` today**, so this has never been exercised in production. It is filed as
**`#todo INVT-06`** in `REQUIREMENTS.md` Deferred.

Two candidate resolutions, neither chosen here (D5 — fixing this is new behaviour, out of
scope for a phase that hardens and tests inventory rather than redesigning it):

1. Create a return `SKUBatch` when `restock=True`, so the units are visible to FEFO again.
2. Refuse `restock=True` outright and require a separate inventory receipt instead.

This deviation is pinned by
`test_credit_note_restock_breaks_the_aggregate_invariant` in
`app/tests/test_inventory_invariants.py`, which asserts the exact shape of the break so it
fails loudly — on purpose — the day someone changes this behaviour.

## 4. Corrections to `codebase/CONCERNS.md`

- **`CONCERNS.md:187`** — claims `remaining_quantity` means "available stock" before dispatch
  and "dispatched quantity" after dispatch. **False.** `order.py:220` decrements it at dispatch
  and `order.py:254` increments it at restore; both treat it as physical units on hand, with no
  branch on order status anywhere in between. This is INVT-03's actual answer: there is no
  status-dependent meaning to resolve, so no column is renamed to fix one.
- **`CONCERNS.md:278`** — claims `(warehouse_id, sku_id)` is not unique in `Inventory`.
  **False.** `PRIMARY KEY (sku_id, warehouse_id)` exists as `inventory_pkey`, verified directly
  in `pg_constraint` (plan 04-01).
- **`CONCERNS.md:190`** — claims `OrderItemBatch` is populated during the `PENDING` →
  `READY_TO_SHIP` transition. **False.** Rows are written at order creation
  (`order.py:177-182`, inside `_reserve_inventory_for_order`, called from
  `create_outgoing_order`); the `READY_TO_SHIP` transition moves no inventory at all
  (plan 04-04).

## 5. Why no rename

`remaining_quantity` (per batch) and `total_quantity` (per sku, warehouse) are the same
physical concept — units of stock physically on hand — carried at two different grains under
two different names. That reads as an inconsistency, and it is a fair thing to notice; it is
the real thing INVT-03 was pointing at, even though `CONCERNS.md`'s specific claim about it
(a status-dependent meaning) is false.

It is not, on its own, grounds for a rename. D5 requires a rename to be justified by a genuine
defect, and there isn't one here: every write path treats both columns consistently, at every
order status, today. A rename touching two models, four service functions, a read path, a
migration and the whole test suite — with no defect behind it — is exactly the scope creep D5
exists to stop. Anyone proposing this rename later should start from this document and bring a
defect, not a naming preference.
