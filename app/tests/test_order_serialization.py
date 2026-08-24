"""Query-count regression tests for ORD-04.

Measured on the pre-fix tree, with a `before_cursor_execute` counter against the SQLite
test engine (see `<baseline>` in `03-04-PLAN.md`):

| Path               | Shape                         | Queries |
|---------------------|-------------------------------|---------|
| get_order_detail    | 1 item, 1 trail               | 11      |
| get_order_detail    | 5 items, 5 trails              | 15      |
| get_order_detail    | 20 items, 12 trails             | 30      |
| get_orders_page     | 5 orders                       | 10      |
| get_orders_page     | 50 orders                      | 10      |

Fitting the growth: cost is `9 + 1 x items` — the growth term is entirely the per-item
lazy load of `OrderItem.taxes` inside `_serialize_order`'s item loop. A second, latent N+1
sits on trail authors: `order.py` fires one `Employee` query per distinct trail author, but
only when that author's `User` row actually has `employee_id` set — the trail-author tests
below deliberately give every author an `Employee` record so that N+1 is exercised, not
hidden.

`get_orders_page` — the actual 50-order list path (`app/services/admin.py:282`) — is
ALREADY constant at 10 queries for both 5 and 50 orders; it does not call
`_serialize_order` at all. Its test here is a regression guard over already-correct code,
not a fix.
"""
import itertools
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import event

from app.models import (
    Account,
    Beat,
    Brand,
    CreditNote,
    CreditNoteItem,
    Employee,
    Order,
    OrderItem,
    OrderItemTax,
    OrderTrail,
    Retailer,
    SKU,
    User,
    Warehouse,
)
from app.models.enums import EmployeeRole, OrderStatus, PaymentMode
from app.services.admin import get_orders_page
from app.services.order import get_order_detail, _serialize_order

_seed_counter = itertools.count()

# Pinned by test_order_detail_query_count_is_bounded — see that test for what "maximal
# shape" means. Any change, up or down, must be a deliberate edit to this constant.
MAXIMAL_SHAPE_QUERY_COUNT = 14
_ADMIN_ACTOR = SimpleNamespace(role=EmployeeRole.ADMIN.value)


@contextmanager
def _count_queries(db):
    """Counts every statement executed against `db`'s bound engine for the duration of
    the `with` block. Caller is responsible for calling `db.expire_all()` beforehand — a
    warm identity map hides lazy loads instead of exercising them.

    Takes the engine from the live session (`db.get_bind()`) rather than importing the
    module-level `app.tests.conftest.engine` name. `app/tests` has no `__init__.py`, so
    pytest's rootless conftest import mechanism loads `conftest.py` under the bare name
    `conftest` for fixture discovery, while `from app.tests.conftest import engine`
    forces a *second*, independent import under the dotted name `app.tests.conftest` —
    two distinct module objects, each running `engine = create_engine(...)` once,
    producing two disconnected Engine instances. The `db` fixture is bound to the first;
    a module-level import of `engine` silently attaches to the second, orphaned one, and
    counts zero queries forever. Deriving the engine from `db.get_bind()` is correct
    regardless of which conftest instance created the session.
    """
    engine = db.get_bind()
    counter = {"n": 0}

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)


def _measure(db, fn, *args, **kwargs):
    db.expire_all()
    with _count_queries(db) as counter:
        result = fn(db, *args, **kwargs)
    return result, counter["n"]


def _make_base(db):
    n = next(_seed_counter)
    brand = Brand(name=f"N1 Brand {n}")
    warehouse = Warehouse(name=f"N1 WH {n}", location="Delhi", state="Delhi")
    retailer = Retailer(name=f"N1 Retailer {n}", state="Delhi")
    db.add_all([brand, warehouse, retailer])
    db.flush()
    sku = SKU(name=f"N1 SKU {n}", brand_id=brand.id, hsn_code="1234")
    db.add(sku)
    db.flush()
    return warehouse, retailer, sku


def _make_employee(db, role=EmployeeRole.ADMIN):
    n = next(_seed_counter)
    emp = Employee(name=f"N1 Employee {n}", email=f"n1-emp-{n}@ascend.com", role=role)
    db.add(emp)
    db.flush()
    return emp


def _make_user(db, *, employee_id=None, role=EmployeeRole.ADMIN):
    n = next(_seed_counter)
    user = User(email=f"n1-user-{n}@ascend.com", password_hash="x", role=role, employee_id=employee_id)
    db.add(user)
    db.flush()
    return user


def _seed_order(
    db,
    *,
    n_items=1,
    n_trails=1,
    n_authors=1,
    with_salesman=False,
    with_driver=False,
    with_beat=False,
    n_payments=0,
    n_credit_notes=0,
):
    """Build an order with the requested shape.

    Every trail author's User carries an `employee_id` pointing at a real Employee — that
    is the only way `order.py`'s per-author Employee lookup fires. A helper that leaves
    `employee_id=None` measures a constant and proves nothing (the baseline table above
    shows exactly that trap: trails cost nothing there only because the seeded authors had
    no employee_id).
    """
    warehouse, retailer, sku = _make_base(db)

    salesman = _make_employee(db, role=EmployeeRole.SALESMAN) if with_salesman else None
    driver = _make_employee(db, role=EmployeeRole.DRIVER) if with_driver else None
    beat = None
    if with_beat:
        n = next(_seed_counter)
        beat = Beat(name=f"N1 Beat {n}", warehouse_id=warehouse.id)
        db.add(beat)
        db.flush()

    order = Order(
        from_entity_type="WAREHOUSE",
        from_entity_id=warehouse.id,
        to_entity_type="RETAILER",
        to_entity_id=retailer.id,
        status=OrderStatus.PENDING,
        salesman_id=salesman.id if salesman else None,
        delivery_driver_id=driver.id if driver else None,
        beat_id=beat.id if beat else None,
    )
    db.add(order)
    db.flush()

    for _ in range(n_items):
        item = OrderItem(
            order_id=order.id,
            sku_id=sku.id,
            quantity=1,
            unit_price=Decimal("100.00"),
            discount_amount=Decimal("0.00"),
        )
        db.add(item)
        db.flush()
        db.add(OrderItemTax(order_item_id=item.id, tax_type="CGST", rate=Decimal("9.00")))
    db.flush()

    if n_trails:
        authors = [
            _make_user(db, employee_id=_make_employee(db).id) for _ in range(max(n_authors, 1))
        ]
        for t in range(n_trails):
            author = authors[t % len(authors)]
            db.add(OrderTrail(
                order_id=order.id,
                order_status=OrderStatus.PENDING,
                description="trail",
                changed_by_id=author.id,
            ))
        db.flush()

    for _ in range(n_payments):
        n = next(_seed_counter)
        db.add(Account(
            order_id=order.id,
            amount=Decimal("10.00"),
            transaction_reference=f"n1-pay-{n}",
            payment_mode=PaymentMode.CASH,
        ))
    db.flush()

    for _ in range(n_credit_notes):
        n = next(_seed_counter)
        cn = CreditNote(order_id=order.id, credit_note_number=f"N1-CN-{n}", applies_to_outstanding=True)
        db.add(cn)
        db.flush()
        db.add(CreditNoteItem(credit_note_id=cn.id, sku_id=sku.id, quantity=1, unit_price=Decimal("10.00")))
    db.flush()

    db.commit()
    db.expire_all()
    return order


def test_order_detail_query_count_does_not_grow_with_items(db):
    order_1 = _seed_order(db, n_items=1, n_trails=1, n_authors=1)
    order_20 = _seed_order(db, n_items=20, n_trails=1, n_authors=1)

    _, count_1 = _measure(db, get_order_detail, order_1.id, _ADMIN_ACTOR)
    _, count_20 = _measure(db, get_order_detail, order_20.id, _ADMIN_ACTOR)

    assert count_1 == count_20, (
        f"query count grew with item count: 1 item = {count_1}, 20 items = {count_20} "
        "(item.taxes is being lazy-loaded per line)"
    )


def test_order_detail_query_count_does_not_grow_with_trail_authors(db):
    order_1 = _seed_order(db, n_items=1, n_trails=1, n_authors=1)
    order_12 = _seed_order(db, n_items=1, n_trails=12, n_authors=6)

    _, count_1 = _measure(db, get_order_detail, order_1.id, _ADMIN_ACTOR)
    _, count_12 = _measure(db, get_order_detail, order_12.id, _ADMIN_ACTOR)

    assert count_1 == count_12, (
        f"query count grew with distinct trail authors: 1 author = {count_1}, "
        f"6 authors = {count_12} (Employee is being queried once per distinct trail author)"
    )


def test_order_detail_query_count_does_not_grow_with_payments_or_credit_notes(db):
    """Compares a light vs a heavy payments/credit-notes shape, both non-empty — not
    zero vs non-zero. A nested `selectinload(Order.credit_notes).selectinload(
    CreditNote.items)` issues its second-level batch query only when the order actually
    has at least one credit note (there is nothing to filter the child query's `IN (...)`
    against otherwise), so an order with zero credit notes costs exactly one query less
    than one with any credit notes at all — a fixed, one-time presence effect, not a
    per-row N+1. It does not scale with the number of credit notes or their items (1
    credit note and 100 both cost the same single batch query), which is exactly what
    this test pins by comparing two non-zero shapes."""
    order_light = _seed_order(db, n_items=1, n_trails=1, n_authors=1, n_payments=1, n_credit_notes=1)
    order_heavy = _seed_order(db, n_items=1, n_trails=1, n_authors=1, n_payments=5, n_credit_notes=5)

    _, count_light = _measure(db, get_order_detail, order_light.id, _ADMIN_ACTOR)
    _, count_heavy = _measure(db, get_order_detail, order_heavy.id, _ADMIN_ACTOR)

    assert count_light == count_heavy, (
        f"query count grew with payments/credit note count: light (1 each) = {count_light}, "
        f"heavy (5 each) = {count_heavy}"
    )


def test_order_detail_query_count_is_bounded(db):
    """Maximal fixed shape: beat, salesman, driver all set; several items; several
    distinct trail authors with employees; payments and credit notes present."""
    order = _seed_order(
        db,
        n_items=5,
        n_trails=6,
        n_authors=3,
        with_salesman=True,
        with_driver=True,
        with_beat=True,
        n_payments=3,
        n_credit_notes=2,
    )

    _, count = _measure(db, get_order_detail, order.id, _ADMIN_ACTOR)

    assert count <= 15, f"maximal shape cost {count} queries, expected <= 15"
    assert count == MAXIMAL_SHAPE_QUERY_COUNT, (
        f"maximal shape cost {count} queries, pinned constant is "
        f"{MAXIMAL_SHAPE_QUERY_COUNT} — update the constant deliberately if this changed "
        "on purpose"
    )


def test_orders_page_query_count_is_constant_for_fifty_orders(db):
    """ROADMAP criterion 4's literal wording, measured against the path that actually
    serves a 50-order list: `get_orders_page` (app/services/admin.py:282). That path
    already batch-loads via selectinload and pre-fetched id maps and never calls
    _serialize_order — this is a regression guard over already-correct code, not a fix.
    """
    for _ in range(5):
        _seed_order(db, n_items=2, n_trails=1, n_authors=1)

    db.expire_all()
    actor = _ADMIN_ACTOR
    with _count_queries(db) as counter:
        get_orders_page(db, actor, limit=5)
    count_5 = counter["n"]

    for _ in range(45):
        _seed_order(db, n_items=2, n_trails=1, n_authors=1)

    db.expire_all()
    with _count_queries(db) as counter:
        get_orders_page(db, actor, limit=50)
    count_50 = counter["n"]

    assert count_5 == count_50, f"get_orders_page grew with order count: 5 = {count_5}, 50 = {count_50}"
    assert count_50 <= 12, f"get_orders_page cost {count_50} queries for 50 orders, expected <= 12"


def test_serialized_shape_is_stable(db):
    """Safety net for the GREEN task: batching must change the query plan and nothing
    else about the payload."""
    warehouse, retailer, sku = _make_base(db)
    salesman = _make_employee(db, role=EmployeeRole.SALESMAN)
    driver = _make_employee(db, role=EmployeeRole.DRIVER)
    beat = Beat(name=f"N1 Beat {next(_seed_counter)}", warehouse_id=warehouse.id)
    db.add(beat)
    db.flush()

    order = Order(
        from_entity_type="WAREHOUSE",
        from_entity_id=warehouse.id,
        to_entity_type="RETAILER",
        to_entity_id=retailer.id,
        status=OrderStatus.PENDING,
        salesman_id=salesman.id,
        delivery_driver_id=driver.id,
        beat_id=beat.id,
    )
    db.add(order)
    db.flush()

    item = OrderItem(order_id=order.id, sku_id=sku.id, quantity=3, unit_price=Decimal("100.00"), discount_amount=Decimal("0.00"))
    db.add(item)
    db.flush()
    db.add(OrderItemTax(order_item_id=item.id, tax_type="CGST", rate=Decimal("9.00")))
    db.add(OrderItemTax(order_item_id=item.id, tax_type="SGST", rate=Decimal("9.00")))
    db.flush()

    # One trail author with an Employee (changed_by_name resolves to the employee's
    # name) and one WITHOUT an employee_id (must fall back to the user's email) — a
    # badly-written join drops exactly this fallback.
    employee_author = _make_user(db, employee_id=_make_employee(db).id)
    emailless_author = _make_user(db, employee_id=None)
    db.add(OrderTrail(order_id=order.id, order_status=OrderStatus.PENDING, description="created", changed_by_id=employee_author.id))
    db.add(OrderTrail(order_id=order.id, order_status=OrderStatus.PENDING, description="touched", changed_by_id=emailless_author.id))
    db.flush()

    db.add(Account(order_id=order.id, amount=Decimal("50.00"), transaction_reference=f"n1-shape-{next(_seed_counter)}", payment_mode=PaymentMode.CASH))
    db.flush()

    cn = CreditNote(order_id=order.id, credit_note_number=f"N1-CN-{next(_seed_counter)}", applies_to_outstanding=True)
    db.add(cn)
    db.flush()
    db.add(CreditNoteItem(credit_note_id=cn.id, sku_id=sku.id, quantity=1, unit_price=Decimal("10.00")))
    db.flush()

    db.commit()
    db.expire_all()

    result = get_order_detail(db, order.id, _ADMIN_ACTOR)

    expected_keys = {
        "id", "from_entity_type", "from_entity_id", "to_entity_type", "to_entity_id",
        "status", "invoice_number", "beat_id", "beat_name", "salesman_id",
        "delivery_driver_id", "delivery_date", "panel_status", "issue_category",
        "description", "payment_status", "items", "warehouse_name", "warehouse_state",
        "retailer_name", "retailer_address_line1", "retailer_city", "retailer_state",
        "retailer_pincode", "retailer_gst_number", "salesman_name", "salesman_phone",
        "delivery_driver_name", "total_amount", "pending_amount", "taxable_value",
        "gst_amount", "subtotal", "grand_total", "payments", "credit_notes", "trails",
        "created_at",
    }
    assert set(result.keys()) == expected_keys

    assert len(result["items"]) == 1
    item_out = result["items"][0]
    assert item_out["sku_name"] == sku.name
    assert item_out["hsn_code"] == "1234"
    assert item_out["taxable_value"] == Decimal("246.00")
    assert item_out["gst_amount"] == Decimal("54.00")
    assert item_out["line_total"] == Decimal("300.00")

    assert len(result["payments"]) == 1
    assert len(result["credit_notes"]) == 1

    assert len(result["trails"]) == 2
    by_desc = {t["description"]: t["changed_by_name"] for t in result["trails"]}
    assert by_desc["created"] == db.query(Employee).filter(Employee.id == employee_author.employee_id).first().name
    assert by_desc["touched"] == emailless_author.email
