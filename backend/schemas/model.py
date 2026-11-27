from pydantic import BaseModel, Field


class ModelTable(BaseModel):
    name: str
    columns: list[str]
    measures: list[str] = []


class ModelMetadata(BaseModel):
    tables: dict[str, ModelTable]
