from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import auth, admin, orders, accounting

app = FastAPI(title="Ascend Foods Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://ascendfoods.in",
        "https://www.ascendfoods.in"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(orders.router, prefix="/orders", tags=["orders"])
app.include_router(accounting.router, prefix="/accounting", tags=["accounting"])
app.include_router(accounting.public_router, tags=["accounting"])
