from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator

LEAD_TYPES = {"RETAILER", "BRAND"}


class LandingLeadCreate(BaseModel):
    # extra="ignore" so a bot posting junk keys gets a clean 201 on the fields we asked for
    # rather than a 422 that tells it what the schema is.
    model_config = ConfigDict(extra="ignore")

    lead_type: str = "RETAILER"
    name: str
    mobile_number: int
    pincode: Optional[int] = None
    note: Optional[str] = None

    @field_validator("lead_type")
    @classmethod
    def normalize_lead_type(cls, value):
        normalized = str(value or "").strip().upper()
        if normalized not in LEAD_TYPES:
            raise ValueError("Lead type must be RETAILER or BRAND.")
        return normalized

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value):
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Name is required.")
        # Bounded so a paste bomb cannot become a row.
        return normalized[:120]

    # Mirrors RetailerCreate.normalize_mobile_number, but required here: a lead with no way
    # to call it back is not a lead.
    @field_validator("mobile_number", mode="before")
    @classmethod
    def normalize_mobile_number(cls, value):
        raw = str(value or "").strip()
        if not raw.isdigit() or len(raw) != 10:
            raise ValueError("Mobile number must be a 10 digit number.")
        return int(raw)

    @field_validator("pincode", mode="before")
    @classmethod
    def normalize_pincode(cls, value):
        if value is None or str(value).strip() == "":
            return None
        raw = str(value).strip()
        if not raw.isdigit() or len(raw) != 6:
            raise ValueError("Pincode must be a 6 digit number.")
        return int(raw)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value):
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized[:500] or None


class LandingLeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    lead_type: str
    name: str
    mobile_number: int
    pincode: Optional[int] = None
    note: Optional[str] = None
    created_at: Optional[datetime] = None
