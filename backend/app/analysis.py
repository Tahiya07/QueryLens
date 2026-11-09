from __future__ import annotations
import json
import math
import re
import duckdb
import pandas as pd
from .intents import parse_intent

MAX_RESULT_ROWS = 500
AGGREGATIONS = {"sum": "SUM", "avg": "AVG", "min": "MIN", "max": "MAX", "count": "COUNT"}


def _ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def analyze(frame: pd.DataFrame, question: str) -> dict:
    op = parse_intent(question, frame)
    if op["kind"] == "clarification":
        return {"understood": False, "clarification": op["message"], "operation": None, "sql": None, "columns": [], "rows": [], "explanation": None, "chart": None}
    if op["kind"] == "preview":
        rows = _records(frame.head(min(op["limit"], MAX_RESULT_ROWS)))
        return _result(op, "SELECT * FROM dataset LIMIT 100", frame.columns.tolist(), rows, "Showing the first 100 rows of this dataset.", {"type":"table"})
    con = duckdb.connect(":memory:")
    con.execute("SET memory_limit='512MB'")
    con.execute("SET threads=2")
    con.execute("SET enable_external_access=false")
    con.register("dataset", frame)
    try:
        if op["kind"] == "correlation":
            columns = op["columns"]
            if any(c not in frame.columns or not pd.api.types.is_numeric_dtype(frame[c]) for c in columns):
                raise ValueError("Correlation requires known numeric columns.")
            sql = "SELECT " + ", ".join(_ident(c) for c in columns) + " FROM dataset LIMIT 50000"
            data = con.execute(sql).df()
            corr = data.corr().round(4)
            rows = [{"column": i, **{str(c): _safe(v) for c, v in row.items()}} for i, row in corr.iterrows()]
            return _result(op, sql, ["column", *columns], rows, f"Pearson correlations calculated for {', '.join(columns)}. Correlation describes association, not causation.", {"type":"heatmap", "columns":columns, "values":corr.values.tolist()})
        if op["kind"] == "distribution":
            c = op["column"]
            if c not in frame.columns or not pd.api.types.is_numeric_dtype(frame[c]): raise ValueError("Distribution requires a known numeric column.")
            sql = f"SELECT {_ident(c)} FROM dataset WHERE {_ident(c)} IS NOT NULL LIMIT 50000"
            values = con.execute(sql).fetchdf()[c]
            hist, edges = __import__("numpy").histogram(values, bins=min(20, max(1, int(len(values) ** .5)))) if len(values) else ([], [])
            rows = [{"bin": f"{edges[i]:.4g}–{edges[i+1]:.4g}", "count": int(hist[i])} for i in range(len(hist))]
            outliers = _outlier_count(values)
            explanation = f"Inspected {len(values)} non-missing values in {c}; the IQR rule flags {outliers} statistical outlier(s). This is an indicator, not proof of an error."
            return _result(op, sql, ["bin", "count"], rows, explanation, {"type":"bar", "x":"bin", "y":"count"})
        if op["kind"] == "timeseries":
            date_col, measure, period = op["date_column"], op["measure"], op["period"]
            if date_col not in frame.columns or not pd.api.types.is_datetime64_any_dtype(frame[date_col]):
                raise ValueError("Time series analysis requires a known date column.")
            if measure not in frame.columns or not pd.api.types.is_numeric_dtype(frame[measure]):
                raise ValueError("Time series analysis requires a known numeric measure.")
            if period not in {"month", "day"}: raise ValueError("Unsupported time interval.")
            sql = f"SELECT DATE_TRUNC('{period}', {_ident(date_col)}) AS period, SUM({_ident(measure)}) AS value FROM dataset WHERE {_ident(date_col)} IS NOT NULL GROUP BY 1 ORDER BY 1 LIMIT {MAX_RESULT_ROWS}"
            totals = con.execute(sql).fetchdf()
            values = totals["value"].tolist()
            rows = []
            for index, record in totals.iterrows():
                current = _safe(record["value"])
                previous = values[index - 1] if index else None
                growth = None if not index or not previous else float((record["value"] / previous - 1) * 100)
                rows.append({"period":str(record["period"]), "value":current, "growth_pct":_safe(growth)})
            label = "monthly" if period == "month" else "daily"
            explanation = f"Summed {measure} by {label} across {len(frame):,} rows, then calculated percent change from the preceding period."
            return _result(op, sql, ["period", "value", "growth_pct"], rows, explanation, {"type":"line", "x":"period", "y":"growth_pct"})
        aggregation = op["aggregation"]
        group = op["group_by"]
        measure = op["measure"]
        if aggregation not in AGGREGATIONS or (group is not None and group not in frame.columns) or (measure and measure not in frame.columns):
            raise ValueError("The requested operation contains an unknown column or operation.")
        if aggregation != "count" and measure and not pd.api.types.is_numeric_dtype(frame[measure]):
            raise ValueError("Choose a numeric measure for this aggregation.")
        if group:
            val = f"{AGGREGATIONS[aggregation]}({_ident(measure)})" if measure else "COUNT(*)"
            sql = f"SELECT {_ident(group)}, {val} AS value FROM dataset GROUP BY {_ident(group)} ORDER BY value DESC NULLS LAST LIMIT {MAX_RESULT_ROWS}"
            columns = [group, "value"]
        else:
            val = f"{AGGREGATIONS[aggregation]}({_ident(measure)})" if measure else "COUNT(*)"
            sql = f"SELECT {val} AS value FROM dataset"
            columns = ["value"]
        rows = _records(con.execute(sql).fetchdf())
        measure_label = measure or "rows"
        agg_label = {"sum":"total", "avg":"average", "min":"minimum", "max":"maximum", "count":"count"}[aggregation]
        explanation = (f"Calculated {agg_label} {measure_label}" + (f" by {group}" if group else " across the dataset") + f" from {len(frame):,} rows.")
        chart = {"type":"bar", "x":group, "y":"value"} if group else {"type":"metric", "value":"value"}
        return _result(op, sql, columns, rows, explanation, chart)
    finally:
        con.close()


def _records(frame):
    out=[]
    for record in frame.to_dict(orient="records"):
        out.append({str(k): _safe(v) for k, v in record.items()})
    return out

def _safe(value):
    if hasattr(value, "item"): value=value.item()
    if value is None: return None
    if isinstance(value, float) and not math.isfinite(value): return None
    if isinstance(value, (str, int, float, bool)): return value
    return str(value)

def _result(op, sql, columns, rows, explanation, chart):
    return {"understood":True, "clarification":None, "operation":op, "sql":sql, "columns":columns, "rows":rows, "explanation":explanation, "chart":chart}

def _outlier_count(values):
    if len(values) < 4: return 0
    q1,q3=values.quantile([.25,.75]); span=q3-q1
    return 0 if span == 0 else int(((values<q1-1.5*span)|(values>q3+1.5*span)).sum())
