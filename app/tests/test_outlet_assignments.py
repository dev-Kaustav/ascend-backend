from datetime import datetime, timedelta

from app.core.security import create_access_token, get_password_hash
from app.models import Employee, Retailer, User
from app.models.enums import AssignmentStatus, EmployeeRole
from app.models.outlet_assignment import OutletAssignment
from app.models.outlet_delivery import OutletDelivery


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


def _employee(db, name: str, role: EmployeeRole) -> Employee:
    employee = Employee(
        name=name,
        email=f"{name.lower().replace(' ', '.')}@example.com",
        role=role,
    )
    db.add(employee)
    db.commit()
    return employee


def _retailer(db, external_id: str) -> Retailer:
    retailer = Retailer(
        name=f"Outlet {external_id}",
        external_id=external_id,
        city="Gurgaon",
        state="Haryana",
        latitude=28.46,
        longitude=77.03,
    )
    db.add(retailer)
    db.commit()
    return retailer


def _setup(db):
    driver = _employee(db, "Primary Driver", EmployeeRole.DRIVER)
    other_driver = _employee(db, "Other Driver", EmployeeRole.DRIVER)
    admin_headers = _auth_header_for(
        db,
        EmployeeRole.ADMIN,
        email="assignment-admin@example.com",
    )
    driver_headers = _auth_header_for(
        db,
        EmployeeRole.DRIVER,
        email="primary-driver@example.com",
        employee=driver,
    )
    other_driver_headers = _auth_header_for(
        db,
        EmployeeRole.DRIVER,
        email="other-driver@example.com",
        employee=other_driver,
    )
    retailers = [_retailer(db, f"OUTLET-{number}") for number in range(1, 4)]
    return {
        "driver": driver,
        "other_driver": other_driver,
        "admin_headers": admin_headers,
        "driver_headers": driver_headers,
        "other_driver_headers": other_driver_headers,
        "retailers": retailers,
    }


def _create_assignment(client, setup, external_ids=None):
    ids = external_ids or [retailer.external_id for retailer in setup["retailers"]]
    response = client.post(
        "/outlet-finder/assignments",
        json={
            "driver_employee_id": setup["driver"].id,
            "external_ids": ids,
            "note": "Morning route",
        },
        headers=setup["admin_headers"],
    )
    assert response.status_code == 200, response.text
    return response.json()


def _delivery(user_id: int, retailer_id: int, created_at: datetime) -> OutletDelivery:
    return OutletDelivery(
        user_id=user_id,
        retailer_id=retailer_id,
        driver_latitude=28.46,
        driver_longitude=77.03,
        distance_m=0.0,
        stored_lat_before=28.46,
        stored_lng_before=77.03,
        retailer_updated=False,
        outer_limit_overridden=False,
        created_at=created_at,
    )


def test_create_assignment_enforces_roles_driver_type_and_nonempty_matches(client, db):
    setup = _setup(db)
    driver_headers = setup["driver_headers"]
    salesman_headers = _auth_header_for(
        db,
        EmployeeRole.SALESMAN,
        email="assignment-salesman@example.com",
    )
    manager_headers = _auth_header_for(
        db,
        EmployeeRole.WAREHOUSE_MANAGER,
        email="assignment-manager@example.com",
    )
    payload = {
        "driver_employee_id": setup["driver"].id,
        "external_ids": ["OUTLET-1"],
    }

    assert client.post(
        "/outlet-finder/assignments",
        json=payload,
        headers=driver_headers,
    ).status_code == 403
    assert client.post(
        "/outlet-finder/assignments",
        json=payload,
        headers=salesman_headers,
    ).status_code == 403
    assert client.post(
        "/outlet-finder/assignments",
        json=payload,
        headers=manager_headers,
    ).status_code == 200

    not_driver = _employee(db, "Not A Driver", EmployeeRole.SALESMAN)
    wrong_employee_response = client.post(
        "/outlet-finder/assignments",
        json={"driver_employee_id": not_driver.id, "external_ids": ["OUTLET-1"]},
        headers=setup["admin_headers"],
    )
    assert wrong_employee_response.status_code == 404

    blank_response = client.post(
        "/outlet-finder/assignments",
        json={"driver_employee_id": setup["driver"].id, "external_ids": [" "]},
        headers=setup["admin_headers"],
    )
    assert blank_response.status_code == 400

    unmatched_response = client.post(
        "/outlet-finder/assignments",
        json={
            "driver_employee_id": setup["driver"].id,
            "external_ids": ["UNKNOWN"],
        },
        headers=setup["admin_headers"],
    )
    assert unmatched_response.status_code == 400


def test_create_assignment_silently_drops_unmatched_outlet_ids_todo_rpt_11(client, db):
    """RPT-11: a pasted route can shrink without identifying which outlets vanished.

    The lookup response already models this with not_found, but assignment creation has
    no equivalent field. D5 records that operational ambiguity without changing the API.
    """
    setup = _setup(db)

    body = _create_assignment(
        client,
        setup,
        ["OUTLET-1", "UNKNOWN-A", "OUTLET-2", "UNKNOWN-B", "OUTLET-3"],
    )

    assert body["total_count"] == 3
    assert {item["external_id"] for item in body["items"]} == {
        "OUTLET-1",
        "OUTLET-2",
        "OUTLET-3",
    }
    assert set(body) == {
        "id",
        "driver_employee_id",
        "driver_name",
        "status",
        "note",
        "created_at",
        "accepted_at",
        "items",
        "delivered_count",
        "total_count",
    }
    assert "UNKNOWN-A" not in response_text(body)
    assert "UNKNOWN-B" not in response_text(body)


def response_text(value) -> str:
    if isinstance(value, dict):
        return " ".join(response_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(response_text(item) for item in value)
    return str(value)


def test_duplicate_external_ids_collapse_to_one_assignment_item(client, db):
    setup = _setup(db)

    body = _create_assignment(
        client,
        setup,
        [" OUTLET-1 ", "OUTLET-1", "OUTLET-1"],
    )

    assert body["total_count"] == 1
    assert [item["external_id"] for item in body["items"]] == ["OUTLET-1"]


def test_list_assignments_lists_all_and_filters_by_status(client, db):
    setup = _setup(db)
    first = _create_assignment(client, setup, ["OUTLET-1"])
    second = _create_assignment(client, setup, ["OUTLET-2"])
    accept_response = client.post(
        f"/outlet-finder/assignments/{first['id']}/accept",
        headers=setup["driver_headers"],
    )
    assert accept_response.status_code == 200

    all_response = client.get(
        "/outlet-finder/assignments",
        headers=setup["admin_headers"],
    )
    accepted_response = client.get(
        "/outlet-finder/assignments",
        params={"status_filter": AssignmentStatus.ACCEPTED.value},
        headers=setup["admin_headers"],
    )
    pending_response = client.get(
        "/outlet-finder/assignments",
        params={"status_filter": AssignmentStatus.PENDING.value},
        headers=setup["admin_headers"],
    )

    assert {row["id"] for row in all_response.json()} == {first["id"], second["id"]}
    assert [row["id"] for row in accepted_response.json()] == [first["id"]]
    assert [row["id"] for row in pending_response.json()] == [second["id"]]


def test_my_assignments_scopes_to_employee_and_requires_linked_profile(client, db):
    setup = _setup(db)
    own = _create_assignment(client, setup, ["OUTLET-1"])
    other_assignment = client.post(
        "/outlet-finder/assignments",
        json={
            "driver_employee_id": setup["other_driver"].id,
            "external_ids": ["OUTLET-2"],
        },
        headers=setup["admin_headers"],
    )
    assert other_assignment.status_code == 200

    mine_response = client.get(
        "/outlet-finder/assignments/mine",
        headers=setup["driver_headers"],
    )
    assert mine_response.status_code == 200
    assert [row["id"] for row in mine_response.json()] == [own["id"]]

    unlinked_headers = _auth_header_for(
        db,
        EmployeeRole.DRIVER,
        email="unlinked-driver@example.com",
    )
    unlinked_response = client.get(
        "/outlet-finder/assignments/mine",
        headers=unlinked_headers,
    )
    assert unlinked_response.status_code == 409


def test_accept_assignment_is_scoped_not_found_safe_and_idempotent(client, db):
    setup = _setup(db)
    assignment = _create_assignment(client, setup, ["OUTLET-1"])

    foreign_response = client.post(
        f"/outlet-finder/assignments/{assignment['id']}/accept",
        headers=setup["other_driver_headers"],
    )
    assert foreign_response.status_code == 403
    assert client.post(
        "/outlet-finder/assignments/999999/accept",
        headers=setup["driver_headers"],
    ).status_code == 404

    first_response = client.post(
        f"/outlet-finder/assignments/{assignment['id']}/accept",
        headers=setup["driver_headers"],
    )
    second_response = client.post(
        f"/outlet-finder/assignments/{assignment['id']}/accept",
        headers=setup["driver_headers"],
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["status"] == AssignmentStatus.ACCEPTED.value
    assert first_response.json()["accepted_at"] is not None
    assert second_response.json()["accepted_at"] == first_response.json()["accepted_at"]


def test_delivered_count_uses_acceptance_window_and_calling_driver_users(client, db):
    setup = _setup(db)
    assignment_body = _create_assignment(client, setup)
    accept_response = client.post(
        f"/outlet-finder/assignments/{assignment_body['id']}/accept",
        headers=setup["driver_headers"],
    )
    assert accept_response.status_code == 200
    db.expire_all()
    assignment = db.query(OutletAssignment).filter(
        OutletAssignment.id == assignment_body["id"]
    ).one()
    accepted_at = assignment.accepted_at
    driver_user = db.query(User).filter(User.email == "primary-driver@example.com").one()
    other_user = db.query(User).filter(User.email == "other-driver@example.com").one()

    db.add_all(
        [
            _delivery(
                driver_user.id,
                setup["retailers"][0].id,
                accepted_at - timedelta(seconds=1),
            ),
            _delivery(
                driver_user.id,
                setup["retailers"][1].id,
                accepted_at + timedelta(seconds=1),
            ),
            _delivery(
                other_user.id,
                setup["retailers"][2].id,
                accepted_at + timedelta(seconds=1),
            ),
        ]
    )
    db.commit()

    response = client.get(
        "/outlet-finder/assignments/mine",
        headers=setup["driver_headers"],
    )

    assert response.status_code == 200
    body = response.json()[0]
    assert body["delivered_count"] == 1
    delivered_by_id = {item["external_id"]: item["delivered"] for item in body["items"]}
    assert delivered_by_id == {
        "OUTLET-1": False,
        "OUTLET-2": True,
        "OUTLET-3": False,
    }


def test_pending_assignment_counts_deliveries_from_any_time_because_there_is_no_window(
    client,
    db,
):
    """A pending assignment has no accepted_at boundary, so old driver rows count."""
    setup = _setup(db)
    assignment = _create_assignment(client, setup, ["OUTLET-1"])
    driver_user = db.query(User).filter(User.email == "primary-driver@example.com").one()
    db.add(
        _delivery(
            driver_user.id,
            setup["retailers"][0].id,
            datetime(2025, 1, 1),
        )
    )
    db.commit()

    response = client.get(
        "/outlet-finder/assignments/mine",
        headers=setup["driver_headers"],
    )

    assert response.status_code == 200
    body = response.json()[0]
    assert body["id"] == assignment["id"]
    assert body["accepted_at"] is None
    assert body["delivered_count"] == 1
    assert body["items"][0]["delivered"] is True
