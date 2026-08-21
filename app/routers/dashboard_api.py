from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import require_accountant, require_roles
from app.schemas.coverage import CoverageResponse
from app.services.coverage import get_coverage
from app.services.daily_sales import get_daily_sales_summary, get_salesman_performance

router = APIRouter()


@router.get("/daily-sales")
def daily_sales_endpoint(
    db: Session = Depends(get_db),
    current_user=Depends(require_accountant),
    warehouse_id: int | None = Query(None),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
):
    return get_daily_sales_summary(db, warehouse_id=warehouse_id, from_date=from_date, to_date=to_date)


@router.get("/salesman-performance")
def salesman_performance_endpoint(
    db: Session = Depends(get_db),
    current_user=Depends(require_accountant),
    warehouse_id: int | None = Query(None),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
):
    return get_salesman_performance(db, warehouse_id=warehouse_id, from_date=from_date, to_date=to_date)


@router.get("/coverage", response_model=CoverageResponse)
def coverage_endpoint(
    db: Session = Depends(get_db),
    current_user=Depends(require_roles("ACCOUNTANT", "WAREHOUSE_MANAGER")),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
    warehouse_id: int | None = Query(None),
    beat_id: int | None = Query(None),
    salesman_id: int | None = Query(None),
):
    try:
        return get_coverage(
            db,
            from_date=from_date,
            to_date=to_date,
            warehouse_id=warehouse_id,
            beat_id=beat_id,
            salesman_id=salesman_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
