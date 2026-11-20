from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.db.session import get_db
from app.services import metrics
from app.services.retailer_overview import RetailerNotFoundError, RetailerScopeError, get_retailer_overview, list_retailers_page

router = APIRouter()


@router.get("")
def list_endpoint(
    search: str | None = None,
    salesman_id: int | None = Query(None, ge=1),
    beat_id: str | None = Query(None, pattern=r"^(none|\d+)$"),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles("ADMIN", "ACCOUNTANT", "SALESMAN")),
):
    """One page of shops, the latest to order first, with their salesman, beat and what they owe.
    A salesman gets their own."""
    try:
        return list_retailers_page(db, current_user, search, salesman_id, beat_id, limit, offset)
    except RetailerScopeError as e:
        raise HTTPException(status_code=403, detail=str(e))


# Declared before /{retailer_id}, which would otherwise try to read "beats" as an id.
@router.get("/beats")
def beats_endpoint(db: Session = Depends(get_db), current_user=Depends(require_roles("ADMIN"))):
    """Each beat with its shop count and what its shops owe, for the beats screen."""
    return metrics.beat_overview(db)


@router.get("/{retailer_id}")
def retailer_overview_endpoint(
    retailer_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles("ADMIN", "ACCOUNTANT", "SALESMAN")),
):
    """One shop: its details, who sells to it, and its report and outstanding."""
    try:
        return get_retailer_overview(db, retailer_id, current_user)
    except RetailerNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RetailerScopeError as e:
        raise HTTPException(status_code=403, detail=str(e))
