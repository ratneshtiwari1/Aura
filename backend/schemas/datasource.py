from pydantic import BaseModel, Field
from typing import Literal


class ExcelUploadResponse(BaseModel):
    sourceId: str
    sourceType: Literal["excel"] = "excel"
    sheets: list[dict]


class SqlConnectionRequest(BaseModel):
    server: str = Field(..., min_length=1)
    database: str = Field(..., min_length=1)
    authentication: Literal["sql", "windows"] = "sql"
    username: str | None = None
    password: str | None = None


class SqlSchemaRequest(BaseModel):
    connectionId: str = Field(..., min_length=1)
