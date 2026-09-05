import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import auth, admin, orders, accounting, bill_labels, imports, dashboard_api, outlet_finder, retailer_requests

app = FastAPI(title="Ascend Foods Backend")

def _load_cors_origins():
    raw_origins = os.getenv("CORS_ORIGINS", "")
    if not raw_origins:
        return []
    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

cors_origins = _load_cors_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(orders.router, prefix="/orders", tags=["orders"])
app.include_router(accounting.router, prefix="/accounting", tags=["accounting"])
app.include_router(accounting.public_router, tags=["accounting"])
app.include_router(bill_labels.router, prefix="/bill-labels", tags=["bill-labels"])
app.include_router(imports.router, prefix="/imports", tags=["imports"])
app.include_router(dashboard_api.router, prefix="/dashboard", tags=["dashboard"])
app.include_router(outlet_finder.router, prefix="/outlet-finder", tags=["outlet-finder"])
app.include_router(retailer_requests.router, prefix="/retailer-requests", tags=["retailer-requests"])
