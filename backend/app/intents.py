"""Conservative deterministic language parser. Only emits a small typed operation vocabulary."""
from __future__ import annotations
import re
from typing import Any
import pandas as pd


def _column(text: str, columns: list[str]) -> str | None:
    lower = text.lower()
    matches = [c for c in columns if re.search(rf"(?<!\w){re.escape(c.lower())}(?!\w)", lower)]
    return max(matches, key=len) if matches else None


def parse_intent(question: str, frame: pd.DataFrame) -> dict[str, Any]:
    q = question.strip().lower()
    if re.search(r"[;]|\b(drop|delete|insert|update|alter|attach|detach|pragma|copy|read_csv|read_parquet)\b", q):
        return _unclear("That request contains unsupported command syntax. Ask for an analytical result in plain language.")
    cols = [str(c) for c in frame.columns]
    numeric = frame.select_dtypes(include="number").columns.tolist()
    dates = frame.select_dtypes(include=["datetime", "datetimetz"]).columns.tolist()
    group = _column(q, cols)
    measures = [c for c in numeric if re.search(rf"(?<!\w){re.escape(c.lower())}(?!\w)", q)]
    measure = measures[0] if measures else (numeric[0] if len(numeric) == 1 else None)

    if any(w in q for w in ("growth", "over time", "month by month")):
        date_col = _column(q, dates) or (str(dates[0]) if len(dates) == 1 else None)
        if not date_col:
            return _unclear("Which date column should I use for the time series?")
        if not measure:
            preferred = [c for c in numeric if re.search(r"revenue|sales|amount|total", str(c), re.I)]
            measure = str(preferred[0]) if len(preferred) == 1 else None
        if not measure:
            return _unclear("Which numeric measure should I compare over time?")
        period = "month" if any(w in q for w in ("month", "monthly")) else "day"
        return {"kind":"timeseries", "date_column":date_col, "measure":measure, "period":period}

    if any(word in q for word in ("correlat", "relationship between")):
        named = measures
        if len(named) < 2:
            mentioned = [_column(q, [c]) for c in numeric]
            named = list(dict.fromkeys(c for c in mentioned if c))
        if len(named) >= 2:
            return {"kind": "correlation", "columns": named[:8]}
        return _unclear("Name at least two numeric columns to compare correlation.")
    if any(w in q for w in ("unusual", "outlier", "anomal", "distribution", "histogram")):
        target = _column(q, [str(c) for c in numeric]) if numeric else None
        if target:
            return {"kind": "distribution", "column": target}
        if len(numeric) == 1:
            return {"kind": "distribution", "column": str(numeric[0])}
        return _unclear("Which numeric column should I inspect for unusual values?")
    if any(w in q for w in ("average", "mean")):
        agg = "avg"
    elif any(w in q for w in ("count", "how many")):
        agg = "count"
    elif any(w in q for w in ("highest", "most", "maximum", "largest", "top")):
        # "Which product generated the highest revenue?" asks for a ranked sum,
        # while "highest price" asks for the maximum observed value.
        agg = "sum" if group and measure and any(w in q for w in ("revenue", "sales", "generated", "total")) else "max"
    elif any(w in q for w in ("lowest", "least", "minimum", "smallest")):
        agg = "min"
    elif any(w in q for w in ("total", "sum", "revenue", "sales")):
        agg = "sum"
    else:
        agg = None
    if agg:
        if agg != "count" and not measure and len(numeric) > 1:
            return _unclear("Which numeric column should I calculate?")
        if not group and agg != "count":
            return _unclear("Which column should I group the result by?")
        if not group:
            return {"kind": "aggregate", "aggregation": "count", "measure": None, "group_by": None}
        return {"kind": "aggregate", "aggregation": agg, "measure": measure or str(numeric[0]) if numeric else None, "group_by": group}
    if any(w in q for w in ("show", "preview", "list", "display")):
        return {"kind": "preview", "limit": 100}
    return _unclear("I couldn’t map that question to a supported operation. Try an average, total, highest value, distribution, correlation, or preview.")


def _unclear(message): return {"kind": "clarification", "message": message}
