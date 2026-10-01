from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.store import StoreBrand, StoreCategory, StoreProduct, StoreProductPage
from app.services import store_catalogue

router = APIRouter()


@router.get("/categories", response_model=list[StoreCategory])
def list_categories(db: Session = Depends(get_db)):
    return store_catalogue.list_store_categories(db)


@router.get("/brands", response_model=list[StoreBrand])
def list_brands(db: Session = Depends(get_db)):
    return store_catalogue.list_store_brands(db)


@router.get("/products", response_model=StoreProductPage, response_model_exclude_none=True)
def list_products(
    category_id: Optional[int] = None,
    brand_id: Optional[int] = None,
    q: Optional[str] = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = store_catalogue.list_store_products(
        db, None, category_id=category_id, brand_id=brand_id, q=q, limit=limit, offset=offset
    )
    return {"items": items, "total": total}


@router.get("/products/{product_id}", response_model=StoreProduct, response_model_exclude_none=True)
def get_product(product_id: int, db: Session = Depends(get_db)):
    try:
        return store_catalogue.get_store_product(db, None, product_id)
    except store_catalogue.StoreProductNotFound:
        raise HTTPException(status_code=404, detail="Product not found")
