from typing import List, Optional

from pydantic import BaseModel, ConfigDict, field_validator


class AccessRuleBase(BaseModel):
    path: str
    roles: Optional[List[str]] = None
    permissions: Optional[List[str]] = None

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        if not value:
            raise ValueError("Path is required.")
        return value.strip()

    @field_validator("roles", "permissions")
    @classmethod
    def normalize_list(cls, value):
        if value is None:
            return []
        return list(value)


class AccessRuleResponse(AccessRuleBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


class AccessRuleUpsert(AccessRuleBase):
    pass
