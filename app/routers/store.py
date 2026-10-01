from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.store import StoreProductPage
from app.services import store_catalogue

router = APIRouter()


@router.get("/products", response_model=StoreProductPage, response_model_exclude_none=True)
def list_products(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = store_catalogue.list_store_products(db, None, limit=limit, offset=offset)
    return {"items": items, "total": total}
