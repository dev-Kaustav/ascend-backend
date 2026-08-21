import pytest

from app.core.security import create_access_token, get_password_hash
from app.models import Employee, Retailer, User
from app.models.enums import EmployeeRole
from app.models.outlet_delivery import OutletDelivery
from app.routers import outlet_finder


BASE_LATITUDE = 28.46
BASE_LONGITUDE = 77.03


def _auth_header_for(
    db,
    role: EmployeeRole,
    *,
    email: str,
    employee: Employee | None = None,
) -> dict:
    user = User(
        email=email,
        password_hash=get_password_hash("password"),
        employee_id=employee.id if employee else None,
        role=role,
    )
    db.add(user)
    db.commit()
    token = create_access_token({"user_id": user.id, "role": role.value})
    return {"Authorization": f"Bearer {token}"}


def _retailer(
    db,
    external_id: str,
    *,
    latitude: float | None = BASE_LATITUDE,
    longitude: float | None = BASE_LONGITUDE,
) -> Retailer:
    retailer = Retailer(
        name=f"Outlet {external_id}",
        external_id=external_id,
        address_line1="Sector 14",
        city="Gurgaon",
        state="Haryana",
        latitude=latitude,
        longitude=longitude,
    )
    db.add(retailer)
    db.commit()
    return retailer


def _mark_payload(external_id: str, latitude: float, **overrides) -> dict:
    payload = {
        "retailer_external_id": external_id,
        "latitude": latitude,
        "longitude": BASE_LONGITUDE,
        "accuracy_m": 20.0,
    }
    payload.update(overrides)
    return payload


def test_lookup_partitions_ids_deduplicates_and_validates_input(client, db):
    headers = _auth_header_for(
        db,
        EmployeeRole.DRIVER,
        email="lookup-driver@example.com",
    )
    _retailer(db, "FOUND")
    _retailer(db, "NO-GPS", latitude=None, longitude=None)

    response = client.post(
        "/outlet-finder/retailers/lookup",
        json={
            "external_ids": [
                " FOUND ",
                "FOUND",
                "  FOUND",
                " NO-GPS ",
                "UNKNOWN",
            ]
        },
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["found"] == [
        {
            "external_id": "FOUND",
            "name": "Outlet FOUND",
            "address": "Sector 14, Gurgaon, Haryana",
            "latitude": BASE_LATITUDE,
            "longitude": BASE_LONGITUDE,
        }
    ]
    assert body["missing_coords"] == [
        {"external_id": "NO-GPS", "name": "Outlet NO-GPS"}
    ]
    assert body["not_found"] == ["UNKNOWN"]

    blank_response = client.post(
        "/outlet-finder/retailers/lookup",
        json={"external_ids": [" ", "\t"]},
        headers=headers,
    )
    assert blank_response.status_code == 400
    assert "non-empty" in blank_response.json()["detail"]

    empty_response = client.post(
        "/outlet-finder/retailers/lookup",
        json={"external_ids": []},
        headers=headers,
    )
    assert empty_response.status_code == 422


def test_haversine_matches_independent_equatorial_distance():
    assert outlet_finder._haversine_m(0.0, 0.0, 1.0, 0.0) == pytest.approx(
        111_194.9,
        abs=0.1,
    )


def test_mark_delivery_enforces_role_accuracy_and_known_retailer(client, db):
    retailer_headers = _auth_header_for(
        db,
        EmployeeRole.RETAILER,
        email="retailer-caller@example.com",
    )
    driver_headers = _auth_header_for(
        db,
        EmployeeRole.DRIVER,
        email="validation-driver@example.com",
    )
    _retailer(db, "VALIDATION")

    retailer_response = client.post(
        "/outlet-finder/deliveries/mark",
        json=_mark_payload("VALIDATION", BASE_LATITUDE),
        headers=retailer_headers,
    )
    assert retailer_response.status_code == 403

    inaccurate_response = client.post(
        "/outlet-finder/deliveries/mark",
        json=_mark_payload("VALIDATION", BASE_LATITUDE, accuracy_m=200.01),
        headers=driver_headers,
    )
    assert inaccurate_response.status_code == 422
    assert "Step outside" in inaccurate_response.json()["detail"]

    unknown_response = client.post(
        "/outlet-finder/deliveries/mark",
        json=_mark_payload("DOES-NOT-EXIST", BASE_LATITUDE),
        headers=driver_headers,
    )
    assert unknown_response.status_code == 404
    assert db.query(OutletDelivery).count() == 0


def test_outlet_without_coordinates_adopts_driver_position_at_accuracy_boundary(client, db):
    headers = _auth_header_for(
        db,
        EmployeeRole.DRIVER,
        email="adopt-driver@example.com",
    )
    retailer = _retailer(db, "ADOPT", latitude=None, longitude=None)
    new_latitude = BASE_LATITUDE + 0.002

    response = client.post(
        "/outlet-finder/deliveries/mark",
        json=_mark_payload("ADOPT", new_latitude, accuracy_m=200.0),
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "delivered": True,
        "distance_m": None,
        "retailer_updated": True,
        "new_latitude": new_latitude,
        "new_longitude": BASE_LONGITUDE,
        "requires_confirmation": False,
        "message": None,
    }
    db.expire_all()
    refreshed = db.query(Retailer).filter(Retailer.id == retailer.id).one()
    delivery = db.query(OutletDelivery).one()
    assert (refreshed.latitude, refreshed.longitude) == (
        new_latitude,
        BASE_LONGITUDE,
    )
    assert delivery.distance_m is None
    assert delivery.stored_lat_before is None
    assert delivery.stored_lng_before is None


def test_driver_inside_threshold_leaves_coordinates_and_writes_audit_row(client, db):
    headers = _auth_header_for(
        db,
        EmployeeRole.DRIVER,
        email="near-driver@example.com",
    )
    user = db.query(User).filter(User.email == "near-driver@example.com").one()
    retailer = _retailer(db, "NEAR")
    driver_latitude = BASE_LATITUDE + 0.0005
    expected_distance = outlet_finder._haversine_m(
        BASE_LATITUDE,
        BASE_LONGITUDE,
        driver_latitude,
        BASE_LONGITUDE,
    )

    response = client.post(
        "/outlet-finder/deliveries/mark",
        json=_mark_payload("NEAR", driver_latitude),
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["delivered"] is True
    assert body["retailer_updated"] is False
    assert body["new_latitude"] is None
    assert body["distance_m"] == pytest.approx(expected_distance)
    db.expire_all()
    refreshed = db.query(Retailer).filter(Retailer.id == retailer.id).one()
    delivery = db.query(OutletDelivery).one()
    assert (refreshed.latitude, refreshed.longitude) == (
        BASE_LATITUDE,
        BASE_LONGITUDE,
    )
    assert delivery.distance_m == pytest.approx(expected_distance)
    assert delivery.stored_lat_before == BASE_LATITUDE
    assert delivery.stored_lng_before == BASE_LONGITUDE
    assert delivery.retailer_updated is False
    assert delivery.user_id == user.id


def test_driver_inside_outer_limit_relocates_outlet_and_preserves_prior_position(client, db):
    headers = _auth_header_for(
        db,
        EmployeeRole.DRIVER,
        email="correction-driver@example.com",
    )
    retailer = _retailer(db, "CORRECT")
    driver_latitude = BASE_LATITUDE + 0.01
    expected_distance = outlet_finder._haversine_m(
        BASE_LATITUDE,
        BASE_LONGITUDE,
        driver_latitude,
        BASE_LONGITUDE,
    )

    response = client.post(
        "/outlet-finder/deliveries/mark",
        json=_mark_payload("CORRECT", driver_latitude),
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["retailer_updated"] is True
    assert response.json()["distance_m"] == pytest.approx(expected_distance)
    db.expire_all()
    refreshed = db.query(Retailer).filter(Retailer.id == retailer.id).one()
    delivery = db.query(OutletDelivery).one()
    assert (refreshed.latitude, refreshed.longitude) == (
        driver_latitude,
        BASE_LONGITUDE,
    )
    assert delivery.stored_lat_before == BASE_LATITUDE
    assert delivery.stored_lng_before == BASE_LONGITUDE
    assert delivery.distance_m == pytest.approx(expected_distance)
    assert delivery.outer_limit_overridden is False


def test_delivery_beyond_outer_limit_is_refused_without_mutating_state(client, db):
    headers = _auth_header_for(
        db,
        EmployeeRole.DRIVER,
        email="far-driver@example.com",
    )
    retailer = _retailer(db, "FAR")
    driver_latitude = BASE_LATITUDE + 0.1
    expected_distance = outlet_finder._haversine_m(
        BASE_LATITUDE,
        BASE_LONGITUDE,
        driver_latitude,
        BASE_LONGITUDE,
    )

    response = client.post(
        "/outlet-finder/deliveries/mark",
        json=_mark_payload("FAR", driver_latitude),
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["delivered"] is False
    assert body["requires_confirmation"] is True
    assert body["retailer_updated"] is False
    assert body["distance_m"] == pytest.approx(expected_distance)
    assert f"{expected_distance / 1000.0:.1f} km" in body["message"]
    db.expire_all()
    refreshed = db.query(Retailer).filter(Retailer.id == retailer.id).one()
    assert (refreshed.latitude, refreshed.longitude) == (
        BASE_LATITUDE,
        BASE_LONGITUDE,
    )
    assert db.query(OutletDelivery).count() == 0


def test_confirmed_outer_limit_override_also_relocates_the_outlet_todo_rpt_08(client, db):
    """RPT-08: confirmation relocates the shared outlet instead of only auditing proof.

    That moves the outlet for every user, so the next driver's distance uses the wrong
    origin. stored_lat_before and stored_lng_before are the only recovery path. D5
    fences this plan to tests because the fix is a product decision: confirmation may
    need to suppress the coordinate rewrite rather than merely rename the behavior.
    """
    headers = _auth_header_for(
        db,
        EmployeeRole.DRIVER,
        email="override-driver@example.com",
    )
    user = db.query(User).filter(User.email == "override-driver@example.com").one()
    retailer = _retailer(db, "OVERRIDE")
    driver_latitude = BASE_LATITUDE + 0.1
    expected_distance = outlet_finder._haversine_m(
        BASE_LATITUDE,
        BASE_LONGITUDE,
        driver_latitude,
        BASE_LONGITUDE,
    )

    response = client.post(
        "/outlet-finder/deliveries/mark",
        json=_mark_payload(
            "OVERRIDE",
            driver_latitude,
            confirm_outer_limit=True,
        ),
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["delivered"] is True
    assert body["retailer_updated"] is True
    assert body["requires_confirmation"] is False
    assert body["distance_m"] == pytest.approx(expected_distance)
    db.expire_all()
    refreshed = db.query(Retailer).filter(Retailer.id == retailer.id).one()
    delivery = db.query(OutletDelivery).one()
    assert (refreshed.latitude, refreshed.longitude) == (
        driver_latitude,
        BASE_LONGITUDE,
    )
    assert delivery.outer_limit_overridden is True
    assert delivery.stored_lat_before == BASE_LATITUDE
    assert delivery.stored_lng_before == BASE_LONGITUDE
    assert delivery.distance_m == pytest.approx(expected_distance)
    assert delivery.user_id == user.id
