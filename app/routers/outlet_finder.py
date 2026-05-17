from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import get_current_active_user
from app.models.retailer import Retailer
from app.schemas.outlet_finder import (
    RetailerLocation,
    RetailerStub,
    RetailerLookupRequest,
    RetailerLookupResponse,
)

router = APIRouter()


@router.post("/retailers/lookup", response_model=RetailerLookupResponse)
def lookup_retailers(
    payload: RetailerLookupRequest,
    db: Session = Depends(get_db),
    _=Depends(get_current_active_user),
):
    deduped = sorted({eid.strip() for eid in payload.external_ids if eid.strip()})
    if not deduped:
        raise HTTPException(status_code=400, detail="external_ids must contain at least one non-empty value")

    rows = db.query(Retailer).filter(Retailer.external_id.in_(deduped)).all()

    found: list[RetailerLocation] = []
    missing_coords: list[RetailerStub] = []
    seen: set[str] = set()

    for r in rows:
        seen.add(r.external_id)
        if r.latitude is None or r.longitude is None:
            missing_coords.append(RetailerStub(external_id=r.external_id, name=r.name))
            continue
        address_parts = [r.address_line1, r.address_line2, r.city, r.state]
        address = ", ".join(p for p in address_parts if p) or None
        found.append(RetailerLocation(
            external_id=r.external_id,
            name=r.name,
            address=address,
            latitude=r.latitude,
            longitude=r.longitude,
        ))

    not_found = [eid for eid in deduped if eid not in seen]

    return RetailerLookupResponse(
        found=found,
        missing_coords=missing_coords,
        not_found=not_found,
    )
