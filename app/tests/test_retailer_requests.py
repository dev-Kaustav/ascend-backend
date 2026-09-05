"""The approval queue that replaced direct retailer creation by salesmen.

The point of the queue is that a salesman cannot conjure an orderable outlet on their own, so
the assertions that matter most here are the negative ones: nothing exists in `retailers` until
an admin approves, and a salesman cannot approve anything — least of all their own request.
"""

from app.core.security import create_access_token, get_password_hash
from app.models import Employee, Retailer, RetailerRequest, User
from app.models.enums import EmployeeRole


def _salesman(db, name):
    employee = Employee(name=name, email=f"{name.lower()}@requests.test", role=EmployeeRole.SALESMAN)
    db.add(employee)
    db.commit()
    user = User(
        email=f"user.{name.lower()}@requests.test",
        password_hash=get_password_hash("password"),
        role=EmployeeRole.SALESMAN,
        employee_id=employee.id,
    )
    db.add(user)
    db.commit()
    token = create_access_token({"user_id": user.id, "role": "SALESMAN"})
    return employee, {"Authorization": f"Bearer {token}"}


def _admin(db):
    user = User(email="admin@requests.test", password_hash=get_password_hash("password"), role=EmployeeRole.ADMIN)
    db.add(user)
    db.commit()
    token = create_access_token({"user_id": user.id, "role": "ADMIN"})
    return {"Authorization": f"Bearer {token}"}


def _submit(client, headers, name="Corner Store", **extra):
    payload = {"name": name, "city": "Delhi", "state": "Delhi"}
    payload.update(extra)
    return client.post("/retailer-requests", json=payload, headers=headers)


def test_pending_request_creates_no_retailer(client, db):
    _, headers = _salesman(db, "Ravi")

    response = _submit(client, headers)

    assert response.status_code == 200
    assert response.json()["status"] == "PENDING"
    assert response.json()["created_retailer_id"] is None
    # The whole point: nothing orderable exists yet.
    assert db.query(Retailer).count() == 0


def test_approval_creates_one_retailer_owned_by_the_requester(client, db):
    employee, headers = _salesman(db, "Ravi")
    admin_headers = _admin(db)
    request_id = _submit(client, headers, name="Approved Store").json()["id"]

    response = client.post(f"/retailer-requests/{request_id}/approve", json={}, headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "APPROVED"
    retailers = db.query(Retailer).all()
    assert len(retailers) == 1
    assert retailers[0].name == "Approved Store"
    assert retailers[0].assigned_salesman_id == employee.id
    # Coverage stays an admin decision even on approval.
    assert retailers[0].beat_id is None
    assert response.json()["created_retailer_id"] == retailers[0].id


def test_a_request_can_only_be_decided_once(client, db):
    _, headers = _salesman(db, "Ravi")
    admin_headers = _admin(db)
    request_id = _submit(client, headers).json()["id"]

    assert client.post(f"/retailer-requests/{request_id}/approve", json={}, headers=admin_headers).status_code == 200
    second = client.post(f"/retailer-requests/{request_id}/approve", json={}, headers=admin_headers)

    assert second.status_code == 409
    # A second approval must not mint a duplicate outlet.
    assert db.query(Retailer).count() == 1
    assert client.post(f"/retailer-requests/{request_id}/reject", json={}, headers=admin_headers).status_code == 409


def test_rejection_records_the_note_and_creates_nothing(client, db):
    _, headers = _salesman(db, "Ravi")
    admin_headers = _admin(db)
    request_id = _submit(client, headers).json()["id"]

    response = client.post(
        f"/retailer-requests/{request_id}/reject",
        json={"note": "Shop already exists under another name"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"
    assert response.json()["review_note"] == "Shop already exists under another name"
    assert db.query(Retailer).count() == 0


def test_salesman_cannot_approve_anything(client, db):
    _, headers = _salesman(db, "Ravi")
    request_id = _submit(client, headers).json()["id"]

    # Not even their own request — that would be the queue approving itself.
    assert client.post(f"/retailer-requests/{request_id}/approve", json={}, headers=headers).status_code == 403
    assert client.post(f"/retailer-requests/{request_id}/reject", json={}, headers=headers).status_code == 403
    assert db.query(Retailer).count() == 0


def test_requests_are_scoped_to_their_owner(client, db):
    _, mine = _salesman(db, "Ravi")
    _, theirs = _salesman(db, "Neha")
    my_id = _submit(client, mine, name="Mine").json()["id"]
    _submit(client, theirs, name="Theirs")

    listed = client.get("/retailer-requests", headers=mine).json()
    assert [row["name"] for row in listed] == ["Mine"]

    assert client.patch(f"/retailer-requests/{my_id}", json={"city": "Noida"}, headers=theirs).status_code == 403
    assert client.post(f"/retailer-requests/{my_id}/withdraw", headers=theirs).status_code == 403

    # An admin sees every salesman's queue.
    all_rows = client.get("/retailer-requests", headers=_admin(db)).json()
    assert {row["name"] for row in all_rows} == {"Mine", "Theirs"}
    assert {row["requested_by_name"] for row in all_rows} == {"Ravi", "Neha"}


def test_owner_can_edit_and_withdraw_while_pending(client, db):
    _, headers = _salesman(db, "Ravi")
    request_id = _submit(client, headers).json()["id"]

    edited = client.patch(f"/retailer-requests/{request_id}", json={"city": "Gurugram"}, headers=headers)
    assert edited.status_code == 200
    assert edited.json()["city"] == "Gurugram"

    withdrawn = client.post(f"/retailer-requests/{request_id}/withdraw", headers=headers)
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == "WITHDRAWN"

    # Withdrawn is decided: no further edits, and an admin can no longer approve it.
    assert client.patch(f"/retailer-requests/{request_id}", json={"city": "X"}, headers=headers).status_code == 409
    assert client.post(f"/retailer-requests/{request_id}/approve", json={}, headers=_admin(db)).status_code == 409
    assert db.query(Retailer).count() == 0


def test_retailer_validators_apply_at_request_time(client, db):
    _, headers = _salesman(db, "Ravi")

    assert _submit(client, headers, mobile_number=123).status_code == 422
    assert _submit(client, headers, pincode=1).status_code == 422
    assert _submit(client, headers, state="Atlantis").status_code == 422
    assert db.query(RetailerRequest).count() == 0


def test_salesman_without_employee_record_cannot_file(client, db):
    user = User(email="orphan@requests.test", password_hash=get_password_hash("password"), role=EmployeeRole.SALESMAN)
    db.add(user)
    db.commit()
    headers = {"Authorization": f"Bearer {create_access_token({'user_id': user.id, 'role': 'SALESMAN'})}"}

    assert _submit(client, headers).status_code == 403
