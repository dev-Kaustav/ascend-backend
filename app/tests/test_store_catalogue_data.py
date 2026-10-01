"""08-02: SKU catalogue fields (image, category, net weight, dormant pack fields) and the
admin-managed categories table (STORE-07, D-02, D-03, D-13, D-14, D-15)."""

import pytest

from app.core.security import create_access_token, get_password_hash
from app.models import Brand, Category, SKU, User
from app.models.enums import EmployeeRole


def _auth_header_for(db, role: EmployeeRole) -> dict:
    user = User(
        email=f"{role.value.lower()}@catalogue.test",
        password_hash=get_password_hash("password"),
        role=role,
    )
    db.add(user)
    db.commit()
    token = create_access_token({"user_id": user.id, "role": role.value})
    return {"Authorization": f"Bearer {token}"}


def _admin(db) -> dict:
    return _auth_header_for(db, EmployeeRole.ADMIN)


def _sku(db, **kwargs) -> SKU:
    brand = Brand(name="Jabsons", poc_name="Asha", poc_phone_number=9876543210)
    db.add(brand)
    db.commit()
    sku = SKU(name=kwargs.pop("name", "Roasted Peanuts 23g"), brand_id=brand.id, **kwargs)
    db.add(sku)
    db.commit()
    return sku


def _category(db, name="Namkeen", sort_order=0, **kwargs) -> Category:
    category = Category(name=name, sort_order=sort_order, **kwargs)
    db.add(category)
    db.commit()
    return category


# ---------------------------------------------------------------------------
# Task 1 - tracer: category + image + net weight on one SKU
# ---------------------------------------------------------------------------

def test_admin_creates_category_with_defaults(client, db):
    resp = client.post("/admin/categories", json={"name": "Roasted Nuts & Seeds"}, headers=_admin(db))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Roasted Nuts & Seeds"
    assert body["sort_order"] == 0
    assert body["icon_url"] is None
    assert isinstance(body["id"], int)


def test_sku_patch_round_trips_through_admin_list_and_lookups(client, db):
    headers = _admin(db)
    sku = _sku(db)
    category = _category(db, "Roasted Nuts & Seeds")
    resp = client.patch(
        f"/admin/skus/{sku.id}",
        json={"image_url": "https://jabsons.com/cdn/x.webp", "category_id": category.id, "net_weight_g": 23},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["image_url"] == "https://jabsons.com/cdn/x.webp"
    assert body["category_id"] == category.id
    assert body["net_weight_g"] == 23

    listed = next(s for s in client.get("/admin/skus", headers=headers).json() if s["id"] == sku.id)
    assert listed["image_url"] == "https://jabsons.com/cdn/x.webp"
    assert listed["category_id"] == category.id
    assert listed["net_weight_g"] == 23

    lookups = client.get("/admin/lookups", headers=headers).json()
    looked_up = next(s for s in lookups["skus"] if s["id"] == sku.id)
    assert looked_up["image_url"] == "https://jabsons.com/cdn/x.webp"
    assert looked_up["category_id"] == category.id
    assert looked_up["net_weight_g"] == 23


@pytest.mark.parametrize(
    "bad_url",
    [
        "javascript:alert(1)",
        "data:image/png;base64,xx",
        "http://x.y/a.png",
        "/relative/path.png",
        "https:///nohost.png",
        "https://x.y/a b.png",
        "https://x.y/" + "a" * 2040,  # 2052 chars
    ],
)
def test_sku_image_url_rejects_non_https(client, db, bad_url):
    headers = _admin(db)
    sku = _sku(db, image_url="https://keep.example/old.png")
    resp = client.patch(f"/admin/skus/{sku.id}", json={"image_url": bad_url}, headers=headers)
    assert resp.status_code == 422, resp.text
    db.expire_all()
    assert db.get(SKU, sku.id).image_url == "https://keep.example/old.png"


def test_sku_image_url_accepts_exactly_2048_chars(client, db):
    headers = _admin(db)
    sku = _sku(db)
    url = "https://x.y/" + "a" * (2048 - len("https://x.y/"))
    assert len(url) == 2048
    resp = client.patch(f"/admin/skus/{sku.id}", json={"image_url": url}, headers=headers)
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("blank", ["", "   "])
def test_sku_image_url_blank_stores_null(client, db, blank):
    headers = _admin(db)
    sku = _sku(db, image_url="https://keep.example/old.png")
    resp = client.patch(f"/admin/skus/{sku.id}", json={"image_url": blank}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["image_url"] is None
    db.expire_all()
    assert db.get(SKU, sku.id).image_url is None


def test_sku_unknown_category_is_400_on_patch(client, db):
    headers = _admin(db)
    sku = _sku(db)
    resp = client.patch(f"/admin/skus/{sku.id}", json={"category_id": 999999}, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Category not found"


def test_sku_unknown_category_is_400_on_create(client, db):
    headers = _admin(db)
    sku = _sku(db)
    payload = _full_sku_payload(sku.brand_id, category_id=999999)
    resp = client.post("/admin/skus", json=payload, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Category not found"


def _full_sku_payload(brand_id, **overrides):
    body = {
        "name": "Peri Peri Makhana 25g",
        "brand_id": brand_id,
        "hsn_code": "2008",
        "mrp": 10,
        "discount_amount": 0,
        "discount_percent": 0,
        "rate": 8,
        "sgst_percent": 0,
        "sgst_amount": 0,
        "cgst_percent": 0,
        "cgst_amount": 0,
        "igst_percent": 0,
        "igst_amount": 0,
        "amount": 8,
        "weight": 0.025,
        "length_cm": 1,
        "width_cm": 1,
        "height_cm": 1,
    }
    body.update(overrides)
    return body


def test_sku_create_accepts_catalogue_fields(client, db):
    headers = _admin(db)
    sku = _sku(db)
    category = _category(db)
    payload = _full_sku_payload(
        sku.brand_id,
        category_id=category.id,
        image_url="https://jabsons.com/a.webp",
        net_weight_g=25,
        pack_type="Case",
        units_per_pack=12,
    )
    resp = client.post("/admin/skus", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["category_id"] == category.id
    assert body["net_weight_g"] == 25
    assert body["pack_type"] == "case"
    assert body["units_per_pack"] == 12


def test_sku_create_without_catalogue_fields_still_works(client, db):
    headers = _admin(db)
    sku = _sku(db)
    resp = client.post("/admin/skus", json=_full_sku_payload(sku.brand_id), headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["image_url"] is None
    assert body["category_id"] is None
    assert body["net_weight_g"] is None
    assert body["pack_type"] is None
    assert body["units_per_pack"] is None


@pytest.mark.parametrize("bad", [0, -5])
def test_sku_net_weight_must_be_at_least_one(client, db, bad):
    headers = _admin(db)
    sku = _sku(db)
    resp = client.patch(f"/admin/skus/{sku.id}", json={"net_weight_g": bad}, headers=headers)
    assert resp.status_code == 422


def test_sku_net_weight_one_is_accepted(client, db):
    headers = _admin(db)
    sku = _sku(db)
    resp = client.patch(f"/admin/skus/{sku.id}", json={"net_weight_g": 1}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["net_weight_g"] == 1


def test_sku_pack_type_is_lowercased_and_restricted(client, db):
    headers = _admin(db)
    sku = _sku(db)
    ok = client.patch(f"/admin/skus/{sku.id}", json={"pack_type": "Case"}, headers=headers)
    assert ok.status_code == 200
    assert ok.json()["pack_type"] == "case"
    bad = client.patch(f"/admin/skus/{sku.id}", json={"pack_type": "crate"}, headers=headers)
    assert bad.status_code == 422


def test_sku_units_per_pack_must_be_positive(client, db):
    headers = _admin(db)
    sku = _sku(db)
    assert client.patch(f"/admin/skus/{sku.id}", json={"units_per_pack": 0}, headers=headers).status_code == 422
    ok = client.patch(f"/admin/skus/{sku.id}", json={"units_per_pack": 6}, headers=headers)
    assert ok.status_code == 200
    assert ok.json()["units_per_pack"] == 6


def test_sku_has_no_description_column():
    """D-34: no description / short-description column is added to skus."""
    assert not any("descr" in c.name for c in SKU.__table__.columns)
