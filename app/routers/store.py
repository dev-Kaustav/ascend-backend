from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.core.store_deps import get_optional_retailer, get_store_retailer, get_store_user
from app.db.session import get_db
from app.schemas.store import (
    StoreCartIn,
    StoreOrderDetail,
    StoreOrderIn,
    StoreOrderPage,
    StoreQuote,
    StoreStockConflict,
    StoreBrand,
    StoreAddress,
    StoreAddressIn,
    StoreAddressUpdate,
    StoreCategory,
    StoreMe,
    StoreProduct,
    StoreProductPage,
    StoreShopIn,
)
from app.services import retailer_onboarding, store_catalogue, store_order
from app.services.invoice_pdf import regenerate_invoice_pdf
from app.services.order import RetailerAccessError

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


@router.get("/me/addresses", response_model=list[StoreAddress])
def list_my_addresses(user=Depends(get_store_retailer), db: Session = Depends(get_db)):
    return retailer_onboarding.list_addresses(db, user.retailer_id)


@router.post("/me/addresses", response_model=StoreAddress, status_code=201)
def add_my_address(
    payload: StoreAddressIn, user=Depends(get_store_retailer), db: Session = Depends(get_db)
):
    return retailer_onboarding.create_address(db, user.retailer_id, payload)


@router.patch("/me/addresses/{address_id}", response_model=StoreAddress)
def edit_my_address(
    address_id: int,
    payload: StoreAddressUpdate,
    user=Depends(get_store_retailer),
    db: Session = Depends(get_db),
):
    try:
        return retailer_onboarding.update_address(db, user.retailer_id, address_id, payload)
    except retailer_onboarding.AddressNotFound:
        raise HTTPException(status_code=404, detail="Address not found")


@router.delete("/me/addresses/{address_id}", status_code=204)
def remove_my_address(
    address_id: int, user=Depends(get_store_retailer), db: Session = Depends(get_db)
):
    try:
        retailer_onboarding.delete_address(db, user.retailer_id, address_id)
    except retailer_onboarding.AddressNotFound:
        raise HTTPException(status_code=404, detail="Address not found")
    except retailer_onboarding.LastAddressError:
        raise HTTPException(status_code=409, detail="Keep at least one delivery address")
    return Response(status_code=204)


STOCK_CHANGED_MESSAGE = "Stock changed for some items. Please review your cart."


def _checkout_http_error(exc):
    """Map the checkout service's refusals onto responses. Raises exc itself when it is not one
    of them, so an unexpected error is never swallowed."""
    if isinstance(exc, store_order.StoreClosed):
        return HTTPException(status_code=503, detail="Store is not taking orders right now")
    if isinstance(exc, retailer_onboarding.AddressNotFound):
        return HTTPException(status_code=404, detail="Address not found")
    if isinstance(exc, store_order.UnknownProducts):
        return HTTPException(
            status_code=422,
            detail={"message": "Some products are not available", "sku_ids": exc.sku_ids},
        )
    if isinstance(exc, store_order.InvalidCart):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, RetailerAccessError):
        return HTTPException(status_code=403, detail=str(exc))
    raise exc


_CHECKOUT_ERRORS = (
    store_order.StoreClosed,
    retailer_onboarding.AddressNotFound,
    store_order.UnknownProducts,
    store_order.InvalidCart,
    RetailerAccessError,
)


@router.post("/cart/quote", response_model=StoreQuote)
def quote_cart(payload: StoreCartIn, user=Depends(get_store_retailer), db: Session = Depends(get_db)):
    try:
        return store_order.quote_cart(db, user, payload)
    except _CHECKOUT_ERRORS as exc:
        raise _checkout_http_error(exc)


@router.post(
    "/orders",
    response_model=StoreOrderDetail,
    status_code=201,
    responses={409: {"model": StoreStockConflict}},
)
def place_order(payload: StoreOrderIn, user=Depends(get_store_retailer), db: Session = Depends(get_db)):
    try:
        order = store_order.place_store_order(db, user, payload)
        return store_order.get_store_order(db, user, order.id)
    except store_order.StockChanged as exc:
        body = StoreStockConflict(message=STOCK_CHANGED_MESSAGE, items=exc.items)
        return JSONResponse(status_code=409, content=body.model_dump())
    except _CHECKOUT_ERRORS as exc:
        raise _checkout_http_error(exc)


@router.get("/orders", response_model=StoreOrderPage)
def list_orders(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(get_store_retailer),
    db: Session = Depends(get_db),
):
    return store_order.list_store_orders(db, user, limit, offset)


@router.get("/orders/{order_id}", response_model=StoreOrderDetail)
def get_order(order_id: int, user=Depends(get_store_retailer), db: Session = Depends(get_db)):
    try:
        return store_order.get_store_order(db, user, order_id)
    except store_order.StoreOrderNotFound:
        raise HTTPException(status_code=404, detail="Order not found")


@router.get("/orders/{order_id}/invoice.pdf")
def get_order_invoice_pdf(order_id: int, user=Depends(get_store_retailer), db: Session = Depends(get_db)):
    try:
        order = store_order.get_store_order_for_invoice(db, user, order_id)
    except store_order.StoreOrderNotFound:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.invoice is None:
        raise HTTPException(status_code=404, detail="No invoice yet")
    output, filename = regenerate_invoice_pdf(db, order.invoice)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(output, media_type="application/pdf", headers=headers)
