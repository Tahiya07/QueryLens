from typing import Any, Literal
from pydantic import BaseModel, Field


class ColumnProfile(BaseModel):
    name: str
    dtype: str
    missing: int
    missing_pct: float
    unique: int
    statistics: dict[str, Any] = Field(default_factory=dict)


class Profile(BaseModel):
    rows: int
    columns: int
    duplicate_rows: int
    numeric_columns: list[str]
    categorical_columns: list[str]
    datetime_columns: list[str]
    columns_profile: list[ColumnProfile]
    correlations: dict[str, dict[str, float | None]]


class DatasetSummary(BaseModel):
    id: str
    name: str
    rows: int
    columns: int
    created_at: str


class DatasetDetail(DatasetSummary):
    profile: Profile
    preview: list[dict[str, Any]]


class TokenResponse(BaseModel):
    token: str
    email: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    llm_enabled: bool


class AnalysisRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class AnalysisResult(BaseModel):
    understood: bool
    clarification: str | None = None
    operation: dict[str, Any] | None = None
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    explanation: str | None = None
    chart: dict[str, Any] | None = None


class ExportRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    format: Literal["csv", "xlsx", "pdf"] = "csv"
