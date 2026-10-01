from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.core.store_deps import get_optional_retailer, get_store_user
from app.db.session import get_db
from app.schemas.store import (
    StoreBrand,
    StoreCategory,
    StoreMe,
    StoreProduct,
    StoreProductPage,
    StoreShopIn,
)
from app.services import retailer_onboarding, store_catalogue

router = APIRouter()

# The body depends on the bearer token (a retailer also sees trade_price and max_orderable), so no
# shared cache may serve one viewer's response to another.
VARY = {"Vary": "Authorization"}


@router.get("/categories", response_model=list[StoreCategory])
def list_categories(
    response: Response,
    viewer=Depends(get_optional_retailer),
    db: Session = Depends(get_db),
):
    response.headers["Vary"] = "Authorization"
    return store_catalogue.list_store_categories(db)


@router.get("/brands", response_model=list[StoreBrand])
def list_brands(
    response: Response,
    viewer=Depends(get_optional_retailer),
    db: Session = Depends(get_db),
):
    response.headers["Vary"] = "Authorization"
    return store_catalogue.list_store_brands(db)


@router.get("/products", response_model=StoreProductPage, response_model_exclude_none=True)
def list_products(
    response: Response,
    category_id: Optional[int] = None,
    brand_id: Optional[int] = None,
    q: Optional[str] = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    viewer=Depends(get_optional_retailer),
    db: Session = Depends(get_db),
):
    response.headers["Vary"] = "Authorization"
    items, total = store_catalogue.list_store_products(
        db, viewer, category_id=category_id, brand_id=brand_id, q=q, limit=limit, offset=offset
    )
    return {"items": items, "total": total}


@router.get("/products/{product_id}", response_model=StoreProduct, response_model_exclude_none=True)
def get_product(
    product_id: int,
    response: Response,
    viewer=Depends(get_optional_retailer),
    db: Session = Depends(get_db),
):
    response.headers["Vary"] = "Authorization"
    try:
        return store_catalogue.get_store_product(db, viewer, product_id)
    except store_catalogue.StoreProductNotFound:
        raise HTTPException(status_code=404, detail="Product not found", headers=VARY)


@router.get("/me", response_model=StoreMe)
def get_me(user=Depends(get_store_user), db: Session = Depends(get_db)):
    return retailer_onboarding.build_store_me(db, user)


@router.put("/me/shop", response_model=StoreMe)
def put_shop(payload: StoreShopIn, user=Depends(get_store_user), db: Session = Depends(get_db)):
    try:
        user = retailer_onboarding.complete_shop(db, user, payload)
    except retailer_onboarding.ShopAddressRequired:
        raise HTTPException(status_code=422, detail="A delivery location is required")
    return retailer_onboarding.build_store_me(db, user)
