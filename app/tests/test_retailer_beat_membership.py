from app.core.security import create_access_token, get_password_hash
from app.models import User, Retailer, Beat
from app.models.enums import EmployeeRole


def _auth_header_for(db, role: EmployeeRole) -> dict:
    user = User(
        email=f"{role.value.lower()}@example.com",
        password_hash=get_password_hash("password"),
        role=role,
    )
    db.add(user)
    db.commit()
    token = create_access_token({"user_id": user.id, "role": role.value})
    return {"Authorization": f"Bearer {token}"}


def _make_retailer(db, name="Test Retailer"):
    retailer = Retailer(name=name)
    db.add(retailer)
    db.commit()
    db.refresh(retailer)
    return retailer


def _make_beat(db, name):
    beat = Beat(name=name)
    db.add(beat)
    db.commit()
    db.refresh(beat)
    return beat


def test_new_retailer_has_no_beat(db):
    retailer = _make_retailer(db)
    assert retailer.beat_id is None


def test_assigning_an_existing_beat_persists_it(client, db):
    admin_headers = _auth_header_for(db, EmployeeRole.ADMIN)
    retailer = _make_retailer(db)
    beat = _make_beat(db, "Route A")

    response = client.patch(
        f"/admin/retailers/{retailer.id}/beat",
        json={"beat_id": beat.id},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["beat_id"] == beat.id
    db.rollback()
    refreshed = db.query(Retailer).filter(Retailer.id == retailer.id).first()
    assert refreshed.beat_id == beat.id


def test_clearing_beat_removes_retailer_from_planned_set(client, db):
    admin_headers = _auth_header_for(db, EmployeeRole.ADMIN)
    retailer = _make_retailer(db)
    beat = _make_beat(db, "Route B")
    retailer.beat_id = beat.id
    db.commit()

    response = client.patch(
        f"/admin/retailers/{retailer.id}/beat",
        json={"beat_id": None},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["beat_id"] is None
    db.rollback()
    refreshed = db.query(Retailer).filter(Retailer.id == retailer.id).first()
    assert refreshed.beat_id is None


def test_unknown_beat_id_refused_with_400_and_nothing_written(client, db):
    admin_headers = _auth_header_for(db, EmployeeRole.ADMIN)
    retailer = _make_retailer(db)

    response = client.patch(
        f"/admin/retailers/{retailer.id}/beat",
        json={"beat_id": 999999},
        headers=admin_headers,
    )

    assert response.status_code == 400
    db.rollback()
    refreshed = db.query(Retailer).filter(Retailer.id == retailer.id).first()
    assert refreshed.beat_id is None


def test_unknown_retailer_id_refused_with_404(client, db):
    admin_headers = _auth_header_for(db, EmployeeRole.ADMIN)
    beat = _make_beat(db, "Route C")

    response = client.patch(
        "/admin/retailers/999999/beat",
        json={"beat_id": beat.id},
        headers=admin_headers,
    )

    assert response.status_code == 404


def test_warehouse_manager_is_refused_with_403(client, db):
    manager_headers = _auth_header_for(db, EmployeeRole.WAREHOUSE_MANAGER)
    retailer = _make_retailer(db)
    beat = _make_beat(db, "Route D")

    response = client.patch(
        f"/admin/retailers/{retailer.id}/beat",
        json={"beat_id": beat.id},
        headers=manager_headers,
    )

    assert response.status_code == 403


def test_salesman_is_refused_with_403(client, db):
    salesman_headers = _auth_header_for(db, EmployeeRole.SALESMAN)
    retailer = _make_retailer(db)
    beat = _make_beat(db, "Route E")

    response = client.patch(
        f"/admin/retailers/{retailer.id}/beat",
        json={"beat_id": beat.id},
        headers=salesman_headers,
    )

    assert response.status_code == 403


def test_admin_succeeds(client, db):
    admin_headers = _auth_header_for(db, EmployeeRole.ADMIN)
    retailer = _make_retailer(db)
    beat = _make_beat(db, "Route F")

    response = client.patch(
        f"/admin/retailers/{retailer.id}/beat",
        json={"beat_id": beat.id},
        headers=admin_headers,
    )

    assert response.status_code == 200


def test_reassigning_replaces_beat_not_adds_second(client, db):
    # D2 — one beat per retailer: after assigning R to beat A and then to beat B,
    # R.beat_id == B.id and there is no second row, no association table, no list.
    admin_headers = _auth_header_for(db, EmployeeRole.ADMIN)
    retailer = _make_retailer(db)
    beat_a = _make_beat(db, "Route G")
    beat_b = _make_beat(db, "Route H")

    first = client.patch(
        f"/admin/retailers/{retailer.id}/beat",
        json={"beat_id": beat_a.id},
        headers=admin_headers,
    )
    assert first.status_code == 200
    assert first.json()["beat_id"] == beat_a.id

    second = client.patch(
        f"/admin/retailers/{retailer.id}/beat",
        json={"beat_id": beat_b.id},
        headers=admin_headers,
    )
    assert second.status_code == 200
    assert second.json()["beat_id"] == beat_b.id

    db.rollback()
    refreshed = db.query(Retailer).filter(Retailer.id == retailer.id).first()
    assert refreshed.beat_id == beat_b.id


def test_list_retailers_returns_beat_id_for_every_retailer(client, db):
    admin_headers = _auth_header_for(db, EmployeeRole.ADMIN)
    beat = _make_beat(db, "Route I")
    assigned = _make_retailer(db, name="Assigned Retailer")
    unassigned = _make_retailer(db, name="Unassigned Retailer")
    assigned.beat_id = beat.id
    db.commit()

    response = client.get("/admin/retailers", headers=admin_headers)

    assert response.status_code == 200
    by_id = {row["id"]: row for row in response.json()}
    assert by_id[assigned.id]["beat_id"] == beat.id
    assert by_id[unassigned.id]["beat_id"] is None
