"""SKU create failures reach the admin as a sentence, and SKUs carry up to four storefront images.

Before app/core/errors.py, a duplicate SKU code was an unhandled IntegrityError: a 500 without CORS
headers, which the browser reported only as "Failed to fetch".
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.core.errors import install_error_handlers
from app.core.security import create_access_token, get_password_hash
from app.models import SKU, User
from app.models.enums import EmployeeRole
from app.tests.store_helpers import make_brand, make_category, make_storefront, stocked_sku


def _admin_headers(db) -> dict:
    user = User(email="admin@skuimages.test", password_hash=get_password_hash("password"), role=EmployeeRole.ADMIN)
    db.add(user)
    db.commit()
    return {"Authorization": f"Bearer {create_access_token({'user_id': user.id, 'role': 'ADMIN'})}"}


def _sku_body(brand_id, **overrides):
    body = {
        "name": "Natkhat Classic 34g",
        "code": "447",
        "brand_id": brand_id,
        "hsn_code": "210690",
        "mrp": 10, "discount_amount": 0, "discount_percent": 0, "rate": 8.47,
        "sgst_percent": 9, "sgst_amount": 0.76, "cgst_percent": 9, "cgst_amount": 0.76,
        "igst_percent": 0, "igst_amount": 0, "amount": 10,
        "weight": 0, "length_cm": 0, "width_cm": 0, "height_cm": 0,
    }
    body.update(overrides)
    return body


def test_duplicate_sku_code_is_a_409_naming_the_code(client, db):
    headers = _admin_headers(db)
    brand = make_brand(db, "Crax")
    assert client.post("/admin/skus", json=_sku_body(brand.id), headers=headers).status_code == 200

    response = client.post("/admin/skus", json=_sku_body(brand.id, name="Another"), headers=headers)

    assert response.status_code == 409
    assert "code" in response.json()["detail"]
    assert "already used" in response.json()["detail"]


def test_unknown_brand_is_a_400_not_a_500(client, db):
    response = client.post("/admin/skus", json=_sku_body(0), headers=_admin_headers(db))

    assert response.status_code == 400
    assert response.json()["detail"] == "Brand not found"


def test_unhandled_error_is_json_and_keeps_cors_headers():
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(CORSMiddleware, allow_origins=["https://ascendfoods.in"], allow_credentials=True)

    @app.get("/boom")
    def boom():
        raise RuntimeError("unexpected")

    response = TestClient(app, raise_server_exceptions=False).get("/boom", headers={"Origin": "https://ascendfoods.in"})

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == "https://ascendfoods.in"
    assert "Server error" in response.json()["detail"]


def test_extra_images_are_validated_compacted_and_cleared(client, db):
    headers = _admin_headers(db)
    brand = make_brand(db, "Crax")

    bad = client.post(
        "/admin/skus", json=_sku_body(brand.id, extra_image_urls=["http://x.test/a.jpg"]), headers=headers
    )
    assert bad.status_code == 422

    too_many = client.post(
        "/admin/skus", json=_sku_body(brand.id, extra_image_urls=["https://x.test/a.jpg"] * 4), headers=headers
    )
    assert too_many.status_code == 422

    created = client.post(
        "/admin/skus",
        json=_sku_body(brand.id, extra_image_urls=["", "https://x.test/b.jpg", " "]),
        headers=headers,
    ).json()
    assert created["extra_image_urls"] == ["https://x.test/b.jpg"]

    cleared = client.patch(
        f"/admin/skus/{created['id']}", json={"extra_image_urls": ["", "", ""]}, headers=headers
    ).json()
    assert cleared["extra_image_urls"] is None


def test_store_product_lists_cover_then_extra_images(client, db):
    warehouse = make_storefront(db)
    sku = stocked_sku(
        db, warehouse=warehouse, brand=make_brand(db), category=make_category(db, "Puffs"),
        name="Cheese Balls", code="338", mrp=10, amount=8,
    )
    sku.extra_image_urls = ["https://x.test/2.jpg", "https://x.test/3.jpg"]
    db.commit()

    product = client.get(f"/store/products/{sku.id}").json()

    assert product["image_urls"] == [sku.image_url, "https://x.test/2.jpg", "https://x.test/3.jpg"]


def test_store_product_without_cover_has_no_images(client, db):
    warehouse = make_storefront(db)
    sku = stocked_sku(
        db, warehouse=warehouse, brand=make_brand(db), category=make_category(db, "Puffs"),
        name="Plain", code="339", mrp=10, amount=8, image_url=None,
    )

    assert client.get(f"/store/products/{sku.id}").json()["image_urls"] == []


def _headers_for(db, role: EmployeeRole) -> dict:
    user = User(email=f"{role.value.lower()}@skuroles.test", password_hash=get_password_hash("password"), role=role)
    db.add(user)
    db.commit()
    return {"Authorization": f"Bearer {create_access_token({'user_id': user.id, 'role': role.value})}"}


def test_accountant_is_refused_by_the_route_not_the_service(client, db):
    brand = make_brand(db, "Crax")

    refused = client.post("/admin/skus", json=_sku_body(brand.id), headers=_headers_for(db, EmployeeRole.ACCOUNTANT))
    allowed = client.post(
        "/admin/skus", json=_sku_body(brand.id), headers=_headers_for(db, EmployeeRole.WAREHOUSE_MANAGER)
    )

    assert refused.status_code == 403
    assert allowed.status_code == 200


def test_weight_and_dimensions_are_optional(client, db):
    brand = make_brand(db, "Crax")
    body = _sku_body(brand.id)
    for field in ("weight", "length_cm", "width_cm", "height_cm"):
        body.pop(field)

    response = client.post("/admin/skus", json=body, headers=_admin_headers(db))

    assert response.status_code == 200
    assert response.json()["weight"] is None


def test_partial_update_keeps_create_constraints_and_leaves_omitted_fields(client, db):
    headers = _admin_headers(db)
    sku = client.post("/admin/skus", json=_sku_body(brand_id=make_brand(db, "Crax").id), headers=headers).json()

    for bad in ({"net_weight_g": 0}, {"image_url": "http://x.test/a.jpg"}, {"pack_type": "crate"}):
        assert client.patch(f"/admin/skus/{sku['id']}", json=bad, headers=headers).status_code == 422, bad

    updated = client.patch(f"/admin/skus/{sku['id']}", json={"name": "Renamed"}, headers=headers).json()
    assert updated["name"] == "Renamed"
    assert updated["code"] == sku["code"]
    assert updated["mrp"] == sku["mrp"]
