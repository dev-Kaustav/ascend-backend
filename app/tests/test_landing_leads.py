"""The marketing site's contact form, which posts with no credentials at all.

The assertions that matter are the containment ones: an anonymous caller can append to
`landing_leads` and can do nothing else — it cannot reach the approval queue that produces
real retailers, and it cannot read back what other people submitted.
"""

from app.core.security import create_access_token, get_password_hash
from app.models import LandingLead, RetailerRequest, User
from app.models.enums import EmployeeRole


def _admin(db):
    user = User(email="admin@leads.test", password_hash=get_password_hash("password"), role=EmployeeRole.ADMIN)
    db.add(user)
    db.commit()
    token = create_access_token({"user_id": user.id, "role": "ADMIN"})
    return {"Authorization": f"Bearer {token}"}


def _lead(**extra):
    payload = {"name": "Sharma Kirana", "mobile_number": "9876543210", "pincode": "122001"}
    payload.update(extra)
    return payload


def test_anonymous_can_submit_a_lead(client, db):
    response = client.post("/public/leads", json=_lead())
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Sharma Kirana"
    assert body["mobile_number"] == 9876543210
    assert body["lead_type"] == "RETAILER"
    assert db.query(LandingLead).count() == 1


def test_lead_never_becomes_a_retailer_request(client, db):
    client.post("/public/leads", json=_lead())
    # The whole reason this table is separate: nothing an anonymous caller posts may land in
    # the queue an admin approves into a real orderable outlet.
    assert db.query(RetailerRequest).count() == 0


def test_repeat_submit_from_same_number_does_not_duplicate(client, db):
    first = client.post("/public/leads", json=_lead())
    second = client.post("/public/leads", json=_lead(note="clicked twice"))
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert db.query(LandingLead).count() == 1


def test_brand_and_retailer_leads_are_kept_apart(client, db):
    client.post("/public/leads", json=_lead())
    response = client.post("/public/leads", json=_lead(lead_type="BRAND"))
    assert response.status_code == 201
    assert response.json()["lead_type"] == "BRAND"
    # Same number, different intent — dedupe must not collapse these into one row.
    assert db.query(LandingLead).count() == 2


def test_bad_mobile_number_is_rejected(client, db):
    assert client.post("/public/leads", json=_lead(mobile_number="123")).status_code == 422
    assert db.query(LandingLead).count() == 0


def test_listing_leads_requires_an_admin(client, db):
    client.post("/public/leads", json=_lead())
    assert client.get("/landing-leads").status_code in (401, 403)
    response = client.get("/landing-leads", headers=_admin(db))
    assert response.status_code == 200
    assert len(response.json()) == 1
