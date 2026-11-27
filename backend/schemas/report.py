from typing import Any, Literal

from pydantic import BaseModel, Field


class ReportPlanRequest(BaseModel):
    sourceId: str = Field(..., min_length=1)
    request: str = Field(..., min_length=1)


class ReportGenerateRequest(BaseModel):
    sourceId: str = Field(..., min_length=1)
    reportSpec: dict[str, Any] | None = None


class ReportStatusResponse(BaseModel):
    reportId: str
    status: str
    message: str
    validation: dict[str, Any] | None = None


class ReportGenerateResponse(BaseModel):
    reportId: str
    status: Literal["generated", "failed"]
    validation: dict[str, Any]
