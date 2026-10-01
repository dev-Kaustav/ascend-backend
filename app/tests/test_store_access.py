"""Retailer isolation and the every-route access matrix (08-13 Task 1; PORT-01, PORT-06).

Anyone with a phone number holds a valid RETAILER token, so identity alone must never open staff
data. Three things are proved here:

1. A retailer cannot read or change another retailer's orders, invoices or addresses (404, never
   403 or 200), and an order's buyer is always the caller.
2. Staff tokens get nothing from the retailer-only /store routes.
3. Every route outside RETAILER_ALLOWED_ROUTES denies a ready RETAILER token with 403. The matrix
   walks the application's own routes, so a route added later without a role guard fails here.
"""
from fastapi.routing import APIRoute

import pytest

from app.main import app
from app.models import Order, RetailerAddress
from app.models.enums import EmployeeRole
from app.tests.store_helpers import auth_headers, make_user, ready_retailer
from app.tests.test_store_invoice import _dispatch, _order_via_api, _store

STAFF_ROLES = [
    EmployeeRole.ADMIN,
    EmployeeRole.SALESMAN,
    EmployeeRole.ACCOUNTANT,
    EmployeeRole.WAREHOUSE_MANAGER,
    EmployeeRole.DRIVER,
]

# Routes a RETAILER token may reach: the storefront itself, authentication, the public lead form,
# the retailer's own order list/detail/status (scoped to the retailer in the service), and the
# two outlet-finder routes keyed on the caller's identity that answer 409 to a non-driver.
# Anything else must refuse a retailer. Never widen this to make the matrix pass: fix the guard.
RETAILER_EXPLICIT_ROUTES = {
    ("POST", "/public/leads"),
    ("GET", "/orders"),
    ("GET", "/orders/{order_id}"),
    ("PATCH", "/orders/{order_id}/status"),
    ("GET", "/outlet-finder/assignments/mine"),
    ("POST", "/outlet-finder/assignments/{assignment_id}/accept"),
}
RETAILER_ALLOWED_PREFIXES = ("/store", "/auth")


def _api_route_methods():
    """Every (method, path template) the application serves.

    FastAPI 0.141 keeps included routers lazy (app.routes holds an _IncludedRouter per
    include_router call), so the flat APIRoute list comes from iter_route_contexts; older
    releases expose the APIRoute objects on app.routes directly.
    """
    try:
        from fastapi.routing import iter_route_contexts

        pairs = [(c.original_route, c.path, c.methods) for c in iter_route_contexts(app.routes)]
    except ImportError:  # pragma: no cover - older FastAPI
        pairs = [(r, r.path, r.methods) for r in app.routes]
    found = []
    for route, path, methods in pairs:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted((methods or set()) - {"HEAD", "OPTIONS"}):
            found.append((method, path))
    return found


def _is_retailer_allowed(method, path):
    return path.startswith(RETAILER_ALLOWED_PREFIXES) or (method, path) in RETAILER_EXPLICIT_ROUTES


RETAILER_ALLOWED_ROUTES = {pair for pair in _api_route_methods() if _is_retailer_allowed(*pair)}


# --- cross-retailer isolation (criterion 4) ---------------------------------------------------


def _two_retailers(client, db):
    """Retailers A and B, each with a saved address and an order; B's order is dispatched."""
    _, sku = _store(db)
    a_user, a_retailer, a_address = ready_retailer(db)
    b_user, b_retailer, b_address = ready_retailer(db)
    a_order = _order_via_api(client, a_user, sku, a_address)
    b_order = _order_via_api(client, b_user, sku, b_address)
    b_invoice = _dispatch(db, b_order)
    return {
        "sku": sku,
        "a": (a_user, a_retailer, a_address, a_order),
        "b": (b_user, b_retailer, b_address, b_order, b_invoice),
    }


def test_a_retailer_gets_404_for_another_retailers_order_and_invoice(client, db):
    world = _two_retailers(client, db)
    a_user = world["a"][0]
    b_order = world["b"][3]
    b_invoice = world["b"][4]
    assert b_invoice is not None  # B's own invoice exists, so a 404 below is isolation, not absence

    order = client.get(f"/store/orders/{b_order}", headers=auth_headers(a_user))
    invoice = client.get(f"/store/orders/{b_order}/invoice.pdf", headers=auth_headers(a_user))

    assert order.status_code == 404
    assert invoice.status_code == 404
    # The owner can read both, so the 404 is about whose they are.
    b_user = world["b"][0]
    assert client.get(f"/store/orders/{b_order}", headers=auth_headers(b_user)).status_code == 200
    assert client.get(f"/store/orders/{b_order}/invoice.pdf", headers=auth_headers(b_user)).status_code == 200


def test_a_retailer_cannot_edit_or_delete_another_retailers_address(client, db):
    world = _two_retailers(client, db)
    a_user = world["a"][0]
    b_address = world["b"][2]
    original_line1 = b_address.line1

    patched = client.patch(
        f"/store/me/addresses/{b_address.id}", json={"line1": "Hijacked"}, headers=auth_headers(a_user)
    )
    deleted = client.delete(f"/store/me/addresses/{b_address.id}", headers=auth_headers(a_user))

    assert patched.status_code == 404
    assert deleted.status_code == 404
    db.expire_all()
    survivor = db.query(RetailerAddress).filter(RetailerAddress.id == b_address.id).one()
    assert survivor.line1 == original_line1


def test_placing_an_order_to_another_retailers_address_is_a_404(client, db):
    world = _two_retailers(client, db)
    a_user = world["a"][0]
    b_address = world["b"][2]
    orders_before = db.query(Order).count()

    response = client.post(
        "/store/orders",
        json={"items": [{"sku_id": world["sku"].id, "quantity": 1}], "address_id": b_address.id},
        headers=auth_headers(a_user),
    )

    assert response.status_code == 404
    assert db.query(Order).count() == orders_before


def test_order_and_address_lists_contain_only_the_callers_rows(client, db):
    world = _two_retailers(client, db)
    a_user, a_retailer, a_address, a_order = world["a"]
    b_user, b_retailer, b_address, b_order, _ = world["b"]

    a_orders = client.get("/store/orders", headers=auth_headers(a_user))
    a_addresses = client.get("/store/me/addresses", headers=auth_headers(a_user))
    b_orders = client.get("/store/orders", headers=auth_headers(b_user))
    b_addresses = client.get("/store/me/addresses", headers=auth_headers(b_user))

    assert [row["id"] for row in a_orders.json()["items"]] == [a_order]
    assert [row["id"] for row in a_addresses.json()] == [a_address.id]
    assert [row["id"] for row in b_orders.json()["items"]] == [b_order]
    assert [row["id"] for row in b_addresses.json()] == [b_address.id]


def test_the_buyer_of_an_order_is_always_the_caller(client, db):
    world = _two_retailers(client, db)
    a_user, a_retailer, a_address, _ = world["a"]
    b_retailer = world["b"][1]

    response = client.post(
        "/store/orders",
        json={
            "items": [{"sku_id": world["sku"].id, "quantity": 1}],
            "address_id": a_address.id,
            "retailer_id": b_retailer.id,
        },
        headers=auth_headers(a_user),
    )

    assert response.status_code == 201, response.text
    order = db.query(Order).filter(Order.id == response.json()["id"]).one()
    assert order.to_entity_type == "RETAILER"
    assert order.to_entity_id == a_retailer.id != b_retailer.id


# --- staff tokens on the retailer-only store routes -------------------------------------------


@pytest.mark.parametrize("role", STAFF_ROLES)
def test_staff_tokens_are_refused_on_every_retailer_only_store_route(client, db, role):
    world = _two_retailers(client, db)
    a_order = world["a"][3]
    staff = make_user(db, role)
    headers = auth_headers(staff)
    quote_body = {"items": [{"sku_id": world["sku"].id, "quantity": 1}]}
    order_body = {**quote_body, "address_id": world["a"][2].id}

    responses = {
        "GET /store/me": client.get("/store/me", headers=headers),
        "GET /store/me/addresses": client.get("/store/me/addresses", headers=headers),
        "POST /store/cart/quote": client.post("/store/cart/quote", json=quote_body, headers=headers),
        "POST /store/orders": client.post("/store/orders", json=order_body, headers=headers),
        "GET /store/orders": client.get("/store/orders", headers=headers),
        "GET /store/orders/{id}": client.get(f"/store/orders/{a_order}", headers=headers),
        "GET /store/orders/{id}/invoice.pdf": client.get(
            f"/store/orders/{a_order}/invoice.pdf", headers=headers
        ),
    }

    assert {name: r.status_code for name, r in responses.items()} == {name: 403 for name in responses}


@pytest.mark.parametrize("role", STAFF_ROLES)
def test_staff_see_the_anonymous_catalogue_shape(client, db, role):
    world = _two_retailers(client, db)
    staff = make_user(db, role)

    staff_view = client.get("/store/products", headers=auth_headers(staff))
    anonymous = client.get("/store/products")
    retailer_view = client.get("/store/products", headers=auth_headers(world["a"][0]))

    assert staff_view.status_code == 200
    items = staff_view.json()["items"]
    assert items, "the catalogue must list the stocked SKU for this check to mean anything"
    assert all("trade_price" not in item and "max_orderable" not in item for item in items)
    assert staff_view.json() == anonymous.json()
    # The retailer's own view does carry trade pricing, so the absence above is the role check.
    assert all("trade_price" in item for item in retailer_view.json()["items"])


# --- outlet-finder lookup and the identity-keyed routes ---------------------------------------


def test_outlet_finder_lookup_is_refused_to_a_retailer(client, db):
    _, _, _ = ready_retailer(db)
    user, _, _ = ready_retailer(db)

    response = client.post(
        "/outlet-finder/retailers/lookup", json={"external_ids": ["X1"]}, headers=auth_headers(user)
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    "role",
    [
        EmployeeRole.ADMIN,
        EmployeeRole.ACCOUNTANT,
        EmployeeRole.WAREHOUSE_MANAGER,
        EmployeeRole.SALESMAN,
        EmployeeRole.DRIVER,
        EmployeeRole.BRAND,
    ],
)
def test_outlet_finder_lookup_still_works_for_the_staff_roles(client, db, role):
    staff = make_user(db, role)

    response = client.post(
        "/outlet-finder/retailers/lookup", json={"external_ids": ["X1"]}, headers=auth_headers(staff)
    )

    assert response.status_code == 200, response.text
    assert response.json()["not_found"] == ["X1"]


def test_identity_keyed_assignment_routes_answer_409_to_a_retailer(client, db):
    user, _, _ = ready_retailer(db)
    headers = auth_headers(user)

    mine = client.get("/outlet-finder/assignments/mine", headers=headers)
    accept = client.post("/outlet-finder/assignments/1/accept", headers=headers)

    assert mine.status_code == 409
    assert accept.status_code == 409


# --- every-route matrix -----------------------------------------------------------------------


def _request_kwargs(method, path, openapi_paths):
    """No body for form/file-upload routes, an empty JSON body for the rest."""
    if method not in {"POST", "PUT", "PATCH"}:
        return {}
    request_body = openapi_paths.get(path, {}).get(method.lower(), {}).get("requestBody")
    if request_body is None:
        return {}
    if "application/json" in request_body.get("content", {}):
        return {"json": {}}
    return {}


def test_every_non_allowlisted_route_denies_a_retailer_token(client, db):
    routes = _api_route_methods()
    assert routes, "no APIRoute was discovered; the matrix would pass vacuously"
    openapi_paths = app.openapi()["paths"]
    user, _, _ = ready_retailer(db)
    headers = auth_headers(user)

    checked, allowlisted, offenders = 0, 0, []
    for method, path in routes:
        if _is_retailer_allowed(method, path):
            allowlisted += 1
            continue
        checked += 1
        url = "".join(
            "1" if part.startswith("{") else part
            for part in _split_template(path)
        )
        response = client.request(method, url, headers=headers, **_request_kwargs(method, path, openapi_paths))
        if response.status_code != 403:
            offenders.append((method, path, response.status_code))

    assert checked + allowlisted == len(routes)
    assert checked > 0 and allowlisted > 0
    assert not offenders, f"routes that did not answer a RETAILER token with 403: {offenders}"


def _split_template(path):
    """'/orders/{order_id}/status' -> ['/orders/', '{order_id}', '/status']."""
    parts, current, depth = [], "", 0
    for char in path:
        if char == "{":
            if current:
                parts.append(current)
            current, depth = "{", 1
        elif char == "}" and depth:
            parts.append(current + "}")
            current, depth = "", 0
        else:
            current += char
    if current:
        parts.append(current)
    return parts


def test_the_explicit_allowlist_names_only_routes_that_exist():
    existing = set(_api_route_methods())
    assert RETAILER_EXPLICIT_ROUTES <= existing, RETAILER_EXPLICIT_ROUTES - existing
    assert RETAILER_ALLOWED_ROUTES, "the /store and /auth routes must be discovered"
    assert any(path.startswith("/store") for _, path in RETAILER_ALLOWED_ROUTES)
