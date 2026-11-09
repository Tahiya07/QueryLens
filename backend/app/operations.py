"""Typed operation validation kept separate from language parsing and execution."""
from __future__ import annotations
import math
from typing import Any, Literal
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class FilterCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str
    operator: Literal["eq","ne","gt","gte","lt","lte","contains","starts_with","is_missing","is_not_missing","between"]
    value: Any = None
    upper_value: Any = None

    @model_validator(mode="after")
    def validate_arity(self):
        if self.operator in {"is_missing","is_not_missing"}:
            if self.value is not None or self.upper_value is not None:
                raise ValueError("Missing-value filters do not take a comparison value.")
        elif self.operator == "between":
            if self.value is None or self.upper_value is None:
                raise ValueError("Between filters require lower and upper values.")
        elif self.value is None or self.upper_value is not None:
            raise ValueError("This filter operator requires exactly one comparison value.")
        return self


class OperationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["aggregate","correlation","distribution","timeseries","preview","filter"]
    aggregation: Literal["sum","avg","min","max","count"] | None = None
    measure: str | None = None
    group_by: str | None = None
    column: str | None = None
    columns: list[str] = Field(default_factory=list, max_length=8)
    correlation_method: Literal["pearson","spearman"] = "pearson"
    date_column: str | None = None
    period: Literal["day","month"] | None = None
    filters: list[FilterCondition] = Field(default_factory=list, max_length=20)
    filter_logic: Literal["and","or"] = "and"
    sort_by: str | None = None
    sort_direction: Literal["asc","desc"] = "desc"
    limit: int = Field(default=100, ge=1, le=500)

    @model_validator(mode="after")
    def validate_shape(self):
        if self.kind == "aggregate":
            if not self.aggregation: raise ValueError("Aggregation operation requires an aggregation.")
            if self.aggregation != "count" and not self.measure: raise ValueError("Choose a numeric measure.")
            if self.sort_by and not self.group_by: raise ValueError("Scalar aggregation results cannot be sorted by a dataset column.")
            if self.sort_by and self.group_by and self.sort_by not in {"value",self.group_by,self.measure}:
                raise ValueError("Sort grouped results by the group column or calculated value.")
        elif self.kind == "distribution" and not self.column:
            raise ValueError("Distribution requires one column.")
        elif self.kind == "correlation" and len(self.columns) < 2:
            raise ValueError("Correlation requires at least two numeric columns.")
        elif self.kind == "timeseries" and not all([self.date_column,self.measure,self.period]):
            raise ValueError("Time series requires a date, measure, and period.")
        elif self.kind == "filter" and not self.filters:
            raise ValueError("Filter operation requires at least one condition.")
        if self.sort_by and self.kind not in {"aggregate","preview","filter"}:
            raise ValueError("Sorting is not supported for this operation.")
        return self


class OperationValidationError(ValueError):
    pass


def _column_exists(frame: pd.DataFrame, name: str | None) -> bool:
    return name is None or name in frame.columns


def _numeric_value(value: Any) -> int | float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise OperationValidationError("Use a numeric value for this filter.") from exc
    if not math.isfinite(parsed):
        raise OperationValidationError("Filter values must be finite numbers.")
    return int(parsed) if parsed.is_integer() else parsed


def _convert_filter(condition: FilterCondition, frame: pd.DataFrame) -> FilterCondition:
    if condition.column not in frame.columns:
        raise OperationValidationError(f"Unknown column: {condition.column}.")
    if condition.operator in {"is_missing","is_not_missing"}:
        return condition
    series = frame[condition.column]
    is_numeric = pd.api.types.is_numeric_dtype(series)
    is_datetime = pd.api.types.is_datetime64_any_dtype(series)
    is_bool = pd.api.types.is_bool_dtype(series)
    is_text = not (is_numeric or is_datetime or is_bool)
    operator = condition.operator
    if operator in {"contains","starts_with"}:
        if not is_text: raise OperationValidationError(f"{operator.replace('_',' ')} requires a text column.")
        return condition.model_copy(update={"value":str(condition.value)})
    if operator in {"gt","gte","lt","lte","between"} and is_text:
        raise OperationValidationError("Ordered comparisons require a numeric or date column.")
    if operator in {"gt","gte","lt","lte","between"} and is_bool:
        raise OperationValidationError("Ordered comparisons are not supported for boolean columns.")
    def convert(value):
        if is_numeric: return _numeric_value(value)
        if is_datetime:
            parsed = pd.to_datetime(value, errors="coerce", utc=False)
            if pd.isna(parsed): raise OperationValidationError("Use a valid date value (ISO format is supported).")
            return pd.Timestamp(parsed).to_pydatetime()
        if is_bool:
            if isinstance(value,bool): return value
            normalized=str(value).strip().lower()
            if normalized not in {"true","false"}: raise OperationValidationError("Use true or false for this filter.")
            return normalized=="true"
        return str(value)
    lower=convert(condition.value)
    upper=convert(condition.upper_value) if operator=="between" else None
    if operator=="between" and lower>upper:
        raise OperationValidationError("The lower bound must be less than or equal to the upper bound.")
    return condition.model_copy(update={"value":lower,"upper_value":upper})


def validate_operation(raw: dict[str, Any], frame: pd.DataFrame) -> OperationPlan:
    try:
        operation = OperationPlan.model_validate(raw)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "The request does not match a supported operation.")
        raise OperationValidationError(message.removeprefix("Value error, ")) from exc
    for name in (operation.measure,operation.group_by,operation.column,operation.date_column):
        if not _column_exists(frame,name): raise OperationValidationError(f"Unknown column: {name}.")
    if operation.sort_by and not (operation.kind=="aggregate" and operation.sort_by=="value") and operation.sort_by not in frame.columns:
        raise OperationValidationError(f"Unknown sort column: {operation.sort_by}.")
    for name in operation.columns:
        if name not in frame.columns: raise OperationValidationError(f"Unknown column: {name}.")
    if operation.measure and not pd.api.types.is_numeric_dtype(frame[operation.measure]):
        raise OperationValidationError(f"Measure {operation.measure} must be numeric.")
    if operation.column and operation.kind == "distribution" and not pd.api.types.is_numeric_dtype(frame[operation.column]):
        raise OperationValidationError("Distribution requires a numeric column.")
    if operation.columns and any(not pd.api.types.is_numeric_dtype(frame[name]) for name in operation.columns):
        raise OperationValidationError("Correlation requires numeric columns.")
    if operation.date_column and not pd.api.types.is_datetime64_any_dtype(frame[operation.date_column]):
        raise OperationValidationError("Time-series analysis requires a date column.")
    filters=[_convert_filter(condition,frame) for condition in operation.filters]
    return operation.model_copy(update={"filters":filters})
