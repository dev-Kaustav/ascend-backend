from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import require_admin
from app.db.session import get_db
from app.models.landing_lead import LandingLead
from app.schemas.landing_lead import LandingLeadCreate, LandingLeadResponse

router = APIRouter()
public_router = APIRouter()

# A second submit from the same number inside this window returns the row already stored
# instead of writing another. Covers the honest double-click and the cheapest kind of flood.
DEDUPE_WINDOW = timedelta(minutes=10)


@public_router.post("/public/leads", response_model=LandingLeadResponse, status_code=201)
def create_landing_lead(payload: LandingLeadCreate, db: Session = Depends(get_db)):
    """Unauthenticated on purpose — this is the marketing site's contact form.

    It can only ever append to `landing_leads`, which nothing downstream reads automatically,
    so the worst a caller can do is give an admin junk to delete.
    """
    cutoff = datetime.now(timezone.utc) - DEDUPE_WINDOW
    recent = (
        db.query(LandingLead)
        .filter(
            LandingLead.mobile_number == payload.mobile_number,
            LandingLead.lead_type == payload.lead_type,
            LandingLead.created_at >= cutoff,
        )
        .order_by(LandingLead.created_at.desc())
        .first()
    )
    if recent:
        return recent

    lead = LandingLead(**payload.model_dump())
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


@router.get("", response_model=list[LandingLeadResponse])
def list_landing_leads(
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
    limit: int = Query(100, ge=1, le=500),
):
    return (
        db.query(LandingLead)
        .order_by(LandingLead.created_at.desc())
        .limit(limit)
        .all()
    )
