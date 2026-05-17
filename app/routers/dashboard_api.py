from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import require_accountant
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
