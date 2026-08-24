"""Exhaustive proof that the order status state machine built in 03-01-PLAN.md behaves
exactly as 03-CONTEXT.md D1 and D2 specify (ORD-01, ORD-02).

Independence rule (load-bearing, do not "simplify" away): `_MATRIX` below is a
hand-transcription of 03-CONTEXT.md D2, typed out independently of
`app/services/order.py::_ALLOWED_TRANSITIONS`. A test that derives its expectations from
the code under test asserts nothing — it can only ever agree with itself. `_MATRIX` is
compared against `_ALLOWED_TRANSITIONS` in exactly one test
(`test_service_table_matches_the_decided_matrix`); every other test in this file consults
`_MATRIX` alone, or drives the real service and checks the resulting database state.

Two halves:
  - Task 1 (this file's top section): the (from, to) x role matrix, proved exhaustively via
    the full 36-cell cross product and the full 7x7=49 pair-x-role cross product.
  - Task 2 (this file's bottom section): behavioural, end-to-end proof that those rules are
    actually wired into `update_order_status` — the happy path, every cancel path, the D1
    reserved-status unreachability, and the D5 invoice-immutability fence.
"""
import itertools
from types import SimpleNamespace

import pytest

from app.models import (
    Brand,
    CreditNote,
    Employee,
    Inventory,
    InventoryTransaction,
    Invoice,
    OrderTrail,
    Retailer,
    SKU,
    SKUBatch,
    User,
    Warehouse,
)
from app.models.enums import EmployeeRole, OrderStatus, TransactionType
from app.schemas.order import OrderCreate, OrderItemCreate, StatusUpdate
from app.services.order import (
    StatusTransitionForbiddenError,
    _ALLOWED_TRANSITIONS,
    _check_role_for_transition,
    allowed_next_statuses,
    create_outgoing_order,
    roles_for_transition,
    update_order_status,
)

# ---------------------------------------------------------------------------------------
# Task 1: the (from, to) x role matrix
# ---------------------------------------------------------------------------------------

# Hand-transcribed from 03-CONTEXT.md D2, verbatim. NOT derived from _ALLOWED_TRANSITIONS.
# Type: dict[tuple[OrderStatus, OrderStatus], frozenset[str]]. Left as a plain assignment
# (no annotation) so the plan's AST verification, which walks ast.Assign nodes, can find it
# — an annotated assignment parses as ast.AnnAssign instead and would silently escape it.
_MATRIX = {
    (OrderStatus.PENDING, OrderStatus.READY_TO_SHIP): frozenset({"WAREHOUSE_MANAGER"}),
    (OrderStatus.READY_TO_SHIP, OrderStatus.OUT_FOR_DELIVERY): frozenset({"DRIVER"}),
    (OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED): frozenset({"DRIVER"}),
    (OrderStatus.PENDING, OrderStatus.CANCELLED): frozenset({"ADMIN", "RETAILER"}),
    # Amended D2 (2026-08-16): a retailer may cancel until the goods leave — READY_TO_SHIP
    # is picked/assigned but not yet dispatched, so this is still self-serve.
    (OrderStatus.READY_TO_SHIP, OrderStatus.CANCELLED): frozenset({"ADMIN", "WAREHOUSE_MANAGER", "RETAILER"}),
    (OrderStatus.OUT_FOR_DELIVERY, OrderStatus.CANCELLED): frozenset({"DRIVER", "ADMIN"}),
    (OrderStatus.DELIVERED, OrderStatus.CANCELLED): frozenset({"ADMIN"}),
}

_ALL_STATUSES: list[OrderStatus] = list(OrderStatus)
_ALL_ROLES: list[EmployeeRole] = list(EmployeeRole)

assert len(_ALL_STATUSES) == 6, "this file's cross products assume exactly six statuses"
assert len(_ALL_ROLES) == 7, "this file's cross products assume exactly seven roles"


def test_service_table_matches_the_decided_matrix():
    """The one place the independent transcription and the implementation meet. Compared
    as whole dicts this catches an added row, a removed row, and a changed role set in a
    single assertion."""
    assert _ALLOWED_TRANSITIONS == _MATRIX


_ALL_PAIRS = list(itertools.product(_ALL_STATUSES, repeat=2))


@pytest.mark.parametrize(
    "frm,to",
    _ALL_PAIRS,
    ids=[f"{frm.value}->{to.value}" for frm, to in _ALL_PAIRS],
)
def test_every_ordered_pair_is_classified(frm, to):
    """Full 36-cell cross product over the six statuses — the 'every transition path' half
    of ORD-02. Self-pairs (PENDING -> PENDING and friends) are included and must be
    forbidden."""
    is_allowed = to in allowed_next_statuses(frm)
    expected_allowed = (frm, to) in _MATRIX
    assert is_allowed == expected_allowed


def test_exactly_seven_pairs_are_allowed():
    """A future row addition to _MATRIX or _ALLOWED_TRANSITIONS must be a deliberate act,
    caught by this count changing."""
    allowed_count = sum(1 for frm, to in _ALL_PAIRS if to in allowed_next_statuses(frm))
    assert allowed_count == 7
    assert len(_MATRIX) == 7


_PAIR_ROLE_CASES = [
    (frm, to, role)
    for (frm, to) in sorted(_MATRIX, key=lambda pair: (pair[0].value, pair[1].value))
    for role in _ALL_ROLES
]


@pytest.mark.parametrize(
    "frm,to,role",
    _PAIR_ROLE_CASES,
    ids=[f"{frm.value}->{to.value}:{role.value}" for frm, to, role in _PAIR_ROLE_CASES],
)
def test_role_is_enforced_per_transition(frm, to, role):
    """7 legal pairs x 7 roles = 49 cases. Uses SimpleNamespace stubs, no database:
    `_check_role_for_transition` reads only `role`/`retailer_id` on the user and
    `status`/`to_entity_id` on the order."""
    allowed_roles = _MATRIX[(frm, to)]
    order = SimpleNamespace(
        status=frm,
        to_entity_id=42,
        delivery_driver_id=7 if role == EmployeeRole.DRIVER else None,
    )
    # A retailer stub is only meaningful as an ownership-passing case here — the role
    # dimension is what this test is about, not ownership (see
    # test_retailer_ownership_is_checked_before_the_role_table for that).
    user = SimpleNamespace(
        role=role.value,
        retailer_id=42 if role == EmployeeRole.RETAILER else None,
        employee_id=7 if role == EmployeeRole.DRIVER else None,
    )

    # ADMIN is the preserved blanket override at app/services/order.py:59-60 (pre-existing
    # behaviour, not a D2 row) — see 03-01-PLAN.md judgement call 1. Named explicitly so a
    # future reader can tell this apart from a _MATRIX-granted role.
    is_admin_override = role == EmployeeRole.ADMIN
    role_is_granted_by_table = role.value in allowed_roles

    if role_is_granted_by_table or is_admin_override:
        _check_role_for_transition(user, to, order)  # must not raise
    else:
        with pytest.raises(StatusTransitionForbiddenError):
            _check_role_for_transition(user, to, order)


def test_retailer_ownership_is_checked_before_the_role_table():
    """A retailer whose retailer_id does not match the order's to_entity_id is refused on
    the one pair where retailers are otherwise allowed, and likewise with no retailer_id
    at all."""
    order = SimpleNamespace(status=OrderStatus.PENDING, to_entity_id=7)

    mismatched_user = SimpleNamespace(role=EmployeeRole.RETAILER.value, retailer_id=999)
    with pytest.raises(StatusTransitionForbiddenError):
        _check_role_for_transition(mismatched_user, OrderStatus.CANCELLED, order)

    no_retailer_user = SimpleNamespace(role=EmployeeRole.RETAILER.value, retailer_id=None)
    with pytest.raises(StatusTransitionForbiddenError):
        _check_role_for_transition(no_retailer_user, OrderStatus.CANCELLED, order)


def test_driver_cannot_transition_unassigned_order():
    order = SimpleNamespace(
        status=OrderStatus.READY_TO_SHIP,
        to_entity_id=7,
        delivery_driver_id=1,
    )
    other_driver = SimpleNamespace(role=EmployeeRole.DRIVER.value, employee_id=2)

    with pytest.raises(StatusTransitionForbiddenError):
        _check_role_for_transition(other_driver, OrderStatus.OUT_FOR_DELIVERY, order)


_FORBIDDEN_SAMPLE = [
    (OrderStatus.PENDING, OrderStatus.DELIVERED),
    (OrderStatus.READY_TO_SHIP, OrderStatus.DELIVERED),
    (OrderStatus.DELIVERED, OrderStatus.READY_TO_SHIP),
    (OrderStatus.CANCELLED, OrderStatus.PENDING),
]


@pytest.mark.parametrize(
    "frm,to",
    _FORBIDDEN_SAMPLE,
    ids=[f"{frm.value}->{to.value}" for frm, to in _FORBIDDEN_SAMPLE],
)
def test_forbidden_pairs_deny_every_non_admin_role(frm, to):
    """An illegal pair fails closed on the role dimension too, even if a caller somehow
    skipped the legality guard in update_order_status."""
    assert (frm, to) not in _MATRIX
    assert roles_for_transition(frm, to) == frozenset()


# ---------------------------------------------------------------------------------------
# Task 2: behavioural end-to-end paths, reserved-status unreachability, and the D5 fence
# ---------------------------------------------------------------------------------------
#
# Seeding helpers duplicated from app/tests/test_invoice_lifecycle.py:19-60 rather than
# imported — that file's helpers are private, and 03-01's gate forbids editing existing
# test files. This module keeps its own itertools.count() to unique emails/names.

_seed_counter = itertools.count()


def _seed_dispatchable_order(db, quantity=5):
    """Create a real Warehouse/Retailer/SKU/driver and a PENDING order against them —
    place_of_supply is NOT NULL on Invoice, so an order dispatched by these tests must
    resolve to a real retailer state, not a bare id."""
    n = next(_seed_counter)
    brand = Brand(name=f"Transitions Brand {n}")
    warehouse = Warehouse(name=f"Transitions WH {n}", location="Delhi", state="Delhi")
    retailer = Retailer(name=f"Transitions Retailer {n}", state="Delhi")
    db.add_all([brand, warehouse, retailer])
    db.commit()

    sku = SKU(name=f"Transitions SKU {n}", brand_id=brand.id)
    db.add(sku)
    db.commit()

    batch = SKUBatch(sku_id=sku.id, warehouse_id=warehouse.id, quantity_received=50, remaining_quantity=50)
    db.add(batch)
    inventory = Inventory(sku_id=sku.id, warehouse_id=warehouse.id, total_quantity=50)
    db.add(inventory)
    driver = Employee(name=f"Transitions Driver {n}", email=f"transitions-driver-{n}@ascend.com", role=EmployeeRole.DRIVER)
    user = User(email=f"transitions-admin-{n}@ascend.com", password_hash="x", role=EmployeeRole.ADMIN)
    db.add_all([driver, user])
    db.commit()

    order = create_outgoing_order(
        db,
        OrderCreate(
            retailer_id=retailer.id,
            warehouse_id=warehouse.id,
            items=[OrderItemCreate(sku_id=sku.id, quantity=quantity, unit_price=100, discount_amount=0)],
        ),
        user,
    )
    return order, driver, user


def _dispatch(db, order, driver, current_user):
    order.delivery_driver_id = driver.id
    db.commit()
    update_order_status(db, order.id, StatusUpdate(status="READY_TO_SHIP"), current_user)
    update_order_status(db, order.id, StatusUpdate(status="OUT_FOR_DELIVERY"), current_user)
    db.refresh(order)


def _user(db, role, retailer_id=None, employee_id=None):
    n = next(_seed_counter)
    user = User(
        email=f"transitions-user-{n}@ascend.com",
        password_hash="x",
        role=role,
        retailer_id=retailer_id,
        employee_id=employee_id,
    )
    db.add(user)
    db.commit()
    return user


def _walk_order_to(db, order, driver, target, wm, drv_user):
    """Drive a freshly-seeded PENDING order to `target` using permitted roles."""
    order.delivery_driver_id = driver.id
    db.commit()
    if target == OrderStatus.PENDING:
        return
    update_order_status(db, order.id, StatusUpdate(status="READY_TO_SHIP"), current_user=wm)
    if target == OrderStatus.READY_TO_SHIP:
        return
    update_order_status(db, order.id, StatusUpdate(status="OUT_FOR_DELIVERY"), current_user=drv_user)
    if target == OrderStatus.OUT_FOR_DELIVERY:
        return
    if target == OrderStatus.DELIVERED:
        update_order_status(db, order.id, StatusUpdate(status="DELIVERED"), current_user=drv_user)
        return
    if target == OrderStatus.CANCELLED:
        update_order_status(db, order.id, StatusUpdate(status="CANCELLED"), current_user=drv_user)
        return
    raise AssertionError(f"_walk_order_to cannot reach {target} via a real transition")


def test_happy_path_walks_the_full_chain_with_the_right_role_at_each_step(db):
    order, driver, _seed_user = _seed_dispatchable_order(db)
    order.delivery_driver_id = driver.id
    db.commit()

    wm = _user(db, EmployeeRole.WAREHOUSE_MANAGER)
    update_order_status(db, order.id, StatusUpdate(status="READY_TO_SHIP"), current_user=wm)
    db.refresh(order)
    assert order.status == OrderStatus.READY_TO_SHIP

    drv_user = _user(db, EmployeeRole.DRIVER, employee_id=driver.id)
    update_order_status(db, order.id, StatusUpdate(status="OUT_FOR_DELIVERY"), current_user=drv_user)
    db.refresh(order)
    assert order.status == OrderStatus.OUT_FOR_DELIVERY

    update_order_status(db, order.id, StatusUpdate(status="DELIVERED"), current_user=drv_user)
    db.refresh(order)
    assert order.status == OrderStatus.DELIVERED

    trails = db.query(OrderTrail).filter(OrderTrail.order_id == order.id).order_by(OrderTrail.id).all()
    # "Order created" (by _seed_user) + one trail row per transition below.
    assert len(trails) == 4
    assert [t.changed_by_id for t in trails[1:]] == [wm.id, drv_user.id, drv_user.id]


_CANCEL_CASES = [
    (OrderStatus.PENDING, EmployeeRole.RETAILER),
    (OrderStatus.READY_TO_SHIP, EmployeeRole.WAREHOUSE_MANAGER),
    (OrderStatus.OUT_FOR_DELIVERY, EmployeeRole.DRIVER),
    (OrderStatus.DELIVERED, EmployeeRole.ADMIN),
]


@pytest.mark.parametrize(
    "from_status,acting_role",
    _CANCEL_CASES,
    ids=[f"{s.value}-by-{r.value}" for s, r in _CANCEL_CASES],
)
def test_cancel_is_reachable_from_every_non_terminal_status(db, from_status, acting_role):
    order, driver, _seed_user = _seed_dispatchable_order(db)
    wm = _user(db, EmployeeRole.WAREHOUSE_MANAGER)
    drv_user = _user(db, EmployeeRole.DRIVER, employee_id=driver.id)
    _walk_order_to(db, order, driver, from_status, wm, drv_user)
    db.refresh(order)
    assert order.status == from_status

    if acting_role == EmployeeRole.RETAILER:
        acting_user = _user(db, EmployeeRole.RETAILER, retailer_id=order.to_entity_id)
    elif acting_role == EmployeeRole.WAREHOUSE_MANAGER:
        acting_user = wm
    elif acting_role == EmployeeRole.DRIVER:
        acting_user = drv_user
    else:
        acting_user = _user(db, EmployeeRole.ADMIN)

    update_order_status(db, order.id, StatusUpdate(status="CANCELLED"), current_user=acting_user)
    db.refresh(order)
    assert order.status == OrderStatus.CANCELLED


def test_driver_doorstep_refusal_restores_dispatched_stock_once(db):
    """The D1 business flow: the driver goes out, the retailer refuses at the doorstep, the
    goods come back. Single-threaded correctness baseline — the concurrent version is
    plan 03-03's job, measured against these exact numbers (see SUMMARY.md)."""
    dispatched_quantity = 5
    order, driver, _seed_user = _seed_dispatchable_order(db, quantity=dispatched_quantity)
    _dispatch(db, order, driver, _seed_user)

    warehouse_id = order.from_entity_id
    sku_id = order.items[0].sku_id
    inventory = (
        db.query(Inventory)
        .filter(Inventory.sku_id == sku_id, Inventory.warehouse_id == warehouse_id)
        .first()
    )
    batch = (
        db.query(SKUBatch)
        .filter(SKUBatch.sku_id == sku_id, SKUBatch.warehouse_id == warehouse_id)
        .first()
    )
    stock_before_cancel = inventory.total_quantity
    batch_remaining_before_cancel = batch.remaining_quantity

    drv_user = _user(db, EmployeeRole.DRIVER, employee_id=driver.id)
    update_order_status(db, order.id, StatusUpdate(status="CANCELLED"), current_user=drv_user)
    db.refresh(inventory)
    db.refresh(batch)

    assert inventory.total_quantity == stock_before_cancel + dispatched_quantity
    assert batch.remaining_quantity == batch_remaining_before_cancel + dispatched_quantity

    returns = (
        db.query(InventoryTransaction)
        .filter(
            InventoryTransaction.sku_id == sku_id,
            InventoryTransaction.warehouse_id == warehouse_id,
            InventoryTransaction.transaction_type == TransactionType.RETURN,
        )
        .all()
    )
    assert len(returns) == 1
    assert returns[0].quantity == dispatched_quantity


def test_rejected_transition_changes_nothing(db):
    order, driver, seed_user = _seed_dispatchable_order(db)
    order.delivery_driver_id = driver.id
    db.commit()

    with pytest.raises(ValueError):
        update_order_status(db, order.id, StatusUpdate(status="DELIVERED"), seed_user)
    db.refresh(order)
    assert order.status == OrderStatus.PENDING
    trail_count_after_value_error = db.query(OrderTrail).filter(OrderTrail.order_id == order.id).count()

    drv_user = _user(db, EmployeeRole.DRIVER, employee_id=driver.id)
    with pytest.raises(StatusTransitionForbiddenError):
        update_order_status(db, order.id, StatusUpdate(status="CANCELLED"), current_user=drv_user)
    db.refresh(order)
    assert order.status == OrderStatus.PENDING
    trail_count_after_forbidden_role = db.query(OrderTrail).filter(OrderTrail.order_id == order.id).count()

    # Neither rejection added a trail row beyond the single "Order created" row.
    assert trail_count_after_value_error == 1
    assert trail_count_after_forbidden_role == 1


_RESERVED_STATUS_REQUEST_KINDS = ["enum", "string"]
_RESERVED_STATUS_CASES = list(itertools.product(_ALL_STATUSES, _RESERVED_STATUS_REQUEST_KINDS))


@pytest.mark.parametrize(
    "current_status,request_kind",
    _RESERVED_STATUS_CASES,
    ids=[f"{s.value}-{k}" for s, k in _RESERVED_STATUS_CASES],
)
def test_the_reserved_status_is_unreachable(db, current_status, request_kind):
    """D1's enforcement. update_order_status coerces strings at order.py:356-360, which is
    the path the HTTP API actually uses, so both the enum member and the plain string are
    exercised."""
    order, driver, seed_user = _seed_dispatchable_order(db)
    wm = _user(db, EmployeeRole.WAREHOUSE_MANAGER)
    drv_user = _user(db, EmployeeRole.DRIVER, employee_id=driver.id)

    if current_status == OrderStatus.RETURNED:
        # Nothing can drive here — RETURNED is unreachable by construction (D1). Plant it.
        order.status = OrderStatus.RETURNED
        db.commit()
    else:
        _walk_order_to(db, order, driver, current_status, wm, drv_user)
    db.refresh(order)
    assert order.status == current_status

    request = (
        StatusUpdate(status=OrderStatus.RETURNED)
        if request_kind == "enum"
        else StatusUpdate(status="RETURNED")
    )
    with pytest.raises(ValueError, match="Invalid status transition"):
        update_order_status(db, order.id, request, seed_user)
    db.refresh(order)
    assert order.status == current_status


@pytest.mark.parametrize(
    "target",
    _ALL_STATUSES,
    ids=[s.value for s in _ALL_STATUSES],
)
def test_an_order_planted_in_the_reserved_status_can_leave_it_for_nothing(db, target):
    """Makes D1's reservation enforced rather than assumed: if a future partial-cancellation
    feature adds an exit from RETURNED, this test fails and forces the decision to be
    explicit."""
    order, _driver, seed_user = _seed_dispatchable_order(db)
    order.status = OrderStatus.RETURNED
    db.commit()

    with pytest.raises(ValueError, match="Invalid status transition"):
        update_order_status(db, order.id, StatusUpdate(status=target.value), seed_user)
    db.refresh(order)
    assert order.status == OrderStatus.RETURNED


def test_cancelling_after_dispatch_leaves_the_issued_invoice_untouched(db):
    """The D5 fence. 03-CONTEXT.md D5 states this is a known, accepted gap awaiting a CA
    answer: cancelling a dispatched order leaves an issued invoice for goods never
    supplied. This test's job is to pin the gap's exact shape so it is visible and so
    nobody closes it by accident — do not read this as a bug to fix.
    """
    order, driver, _seed_user = _seed_dispatchable_order(db)
    _dispatch(db, order, driver, _seed_user)

    invoice_id = order.invoice.id
    invoice_number = order.invoice.invoice_number
    invoice_status = order.invoice.status
    invoice_grand_total = order.invoice.grand_total

    admin = _user(db, EmployeeRole.ADMIN)
    update_order_status(db, order.id, StatusUpdate(status="CANCELLED"), current_user=admin)
    db.refresh(order)

    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    assert invoice is not None
    assert invoice.invoice_number == invoice_number
    assert invoice.status == invoice_status == "ISSUED"
    assert invoice.grand_total == invoice_grand_total

    assert db.query(CreditNote).filter(CreditNote.order_id == order.id).count() == 0


def test_delivered_orders_always_have_a_delivery_driver(db):
    """The invariant holds structurally because DELIVERED is reachable only via
    READY_TO_SHIP, which refuses without a driver (order.py:396-397) — this asserts the
    invariant rather than adding a redundant second guard (see 03-01-PLAN.md judgement
    call 2)."""
    for _ in range(2):
        order, driver, _seed_user = _seed_dispatchable_order(db)
        wm = _user(db, EmployeeRole.WAREHOUSE_MANAGER)
        drv_user = _user(db, EmployeeRole.DRIVER, employee_id=driver.id)
        _walk_order_to(db, order, driver, OrderStatus.DELIVERED, wm, drv_user)
        db.refresh(order)
        assert order.status == OrderStatus.DELIVERED
        assert order.delivery_driver_id is not None
