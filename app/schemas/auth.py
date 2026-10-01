from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str

class RefreshRequest(BaseModel):
    refresh_token: str

class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    role: str
    is_active: bool

class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class RetailerFirebaseLoginRequest(BaseModel):
    # The phone number is taken only from the verified token (D-19), never from the body.
    id_token: str = Field(min_length=1, max_length=4096)

class RetailerShopPrefill(BaseModel):
    shop_name: str
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[int] = None
    gst_number: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class RetailerLoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    onboarding: Literal["new", "confirm", "ready"]
    prefill: Optional[RetailerShopPrefill] = None
