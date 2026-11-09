from __future__ import annotations
import numpy as np
import pandas as pd


def profile(frame: pd.DataFrame) -> dict:
    numeric = frame.select_dtypes(include="number").columns.tolist()
    datetime = frame.select_dtypes(include=["datetime", "datetimetz"]).columns.tolist()
    categorical = [c for c in frame.columns if c not in numeric and c not in datetime]
    cols = []
    for name in frame.columns:
        series = frame[name]
        stats = {}
        if name in numeric:
            values = series.dropna()
            stats = {
                "mean": _num(values.mean()), "median": _num(values.median()),
                "std": _num(values.std()), "min": _num(values.min()), "max": _num(values.max()),
                "outlier_count": _outliers(values),
            }
        elif name in categorical:
            stats["top_values"] = {str(k): int(v) for k, v in series.value_counts(dropna=True).head(8).items()}
        cols.append({"name": name, "dtype": str(series.dtype), "missing": int(series.isna().sum()),
                     "missing_pct": round(float(series.isna().mean() * 100), 2),
                     "unique": int(series.nunique(dropna=True)), "statistics": stats})
    corr = frame[numeric].corr().replace({np.nan: None}).to_dict() if len(numeric) > 1 else {}
    return {"rows": len(frame), "columns": len(frame.columns), "duplicate_rows": int(frame.duplicated().sum()),
            "numeric_columns": numeric, "categorical_columns": categorical, "datetime_columns": datetime,
            "columns_profile": cols, "correlations": corr}


def _num(value):
    return None if pd.isna(value) else float(value)


def _outliers(values: pd.Series) -> int:
    if len(values) < 4: return 0
    q1, q3 = values.quantile([.25, .75])
    span = q3 - q1
    if span == 0: return 0
    return int(((values < q1 - 1.5 * span) | (values > q3 + 1.5 * span)).sum())
