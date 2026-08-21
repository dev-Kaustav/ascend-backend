from typing import Optional

from pydantic import BaseModel, ConfigDict


class CoverageBeatRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    beat_id: int
    beat_name: str
    warehouse_id: Optional[int]
    planned: int
    billed: int
    coverage_percent: Optional[float]


class CoverageSalesmanRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    salesman_id: Optional[int]
    salesman_name: str
    planned: int
    billed: int
    coverage_percent: Optional[float]


class CoverageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_date: Optional[str]
    to_date: Optional[str]
    planned: int
    billed: int
    # A ratio with no planned outlets is undefined, not a measured zero.
    coverage_percent: Optional[float]
    retailers_without_beat: int
    by_beat: list[CoverageBeatRow]
    by_salesman: list[CoverageSalesmanRow]
