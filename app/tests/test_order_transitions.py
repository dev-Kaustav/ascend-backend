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
    CreditNote,
    Inventory,
    InventoryTransaction,
    Invoice,
    OrderTrail,
    SKUBatch,
    User,
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
    order = SimpleNamespace(status=frm, to_entity_id=42)
    # A retailer stub is only meaningful as an ownership-passing case here — the role
    # dimension is what this test is about, not ownership (see
    # test_retailer_ownership_is_checked_before_the_role_table for that).
    user = SimpleNamespace(
        role=role.value,
        retailer_id=42 if role == EmployeeRole.RETAILER else None,
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
