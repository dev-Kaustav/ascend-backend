from pydantic import BaseModel, ConfigDict
from typing import Optional

class BrandCreate(BaseModel):
    name: str
    contact_info: Optional[str]

class BrandResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    contact_info: Optional[str]

class WarehouseCreate(BaseModel):
    name: str
    location: Optional[str]

class WarehouseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    location: Optional[str]

class EmployeeCreate(BaseModel):
    name: str
    email: str
    role: str
    warehouse_id: Optional[int]

class EmployeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str
    role: str
    warehouse_id: Optional[int]

class RetailerCreate(BaseModel):
    name: str
    contact_info: Optional[str]
    assigned_salesman_id: Optional[int]

class RetailerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    contact_info: Optional[str]
    assigned_salesman_id: Optional[int]

class SKUCreate(BaseModel):
    name: str
    brand_id: int
    description: Optional[str]
    unit: Optional[str]

class SKUResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    brand_id: int
    description: Optional[str]
    unit: Optional[str]

class IncomingOrderItem(BaseModel):
    sku_id: int
    quantity: float
    batch_number: str
    mfg_date: Optional[str]
    expiry_date: Optional[str]

class IncomingOrderCreate(BaseModel):
    brand_id: int
    warehouse_id: int
    items: list[IncomingOrderItem]

class InventoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    sku_id: int
    warehouse_id: int
    total_quantity: float
