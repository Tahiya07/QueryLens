from __future__ import annotations
import json
import math
import os
import re
import threading
from threading import Event, Timer
import duckdb
import pandas as pd
from .intents import parse_intent
from .operations import OperationValidationError, validate_operation

MAX_RESULT_ROWS = 500
AGGREGATIONS = {"sum": "SUM", "avg": "AVG", "min": "MIN", "max": "MAX", "count": "COUNT"}
QUERY_TIMEOUT_SECONDS = float(os.getenv("QUERYLENS_QUERY_TIMEOUT_SECONDS", "5"))
if not 0.05 <= QUERY_TIMEOUT_SECONDS <= 120:
    raise RuntimeError("QUERYLENS_QUERY_TIMEOUT_SECONDS must be between 0.05 and 120 seconds.")
MAX_CONCURRENT_ANALYSES = int(os.getenv("QUERYLENS_MAX_CONCURRENT_ANALYSES", "2"))
if not 1 <= MAX_CONCURRENT_ANALYSES <= 32:
    raise RuntimeError("QUERYLENS_MAX_CONCURRENT_ANALYSES must be between 1 and 32.")
DUCKDB_MEMORY_LIMIT = os.getenv("QUERYLENS_DUCKDB_MEMORY_LIMIT", "512MB").upper()
if not re.fullmatch(r"[1-9]\d{0,3}(MB|GB)", DUCKDB_MEMORY_LIMIT):
    raise RuntimeError("QUERYLENS_DUCKDB_MEMORY_LIMIT must be a value such as 512MB or 1GB.")
_memory_amount = int(re.match(r"\d+", DUCKDB_MEMORY_LIMIT).group())
_memory_mb = _memory_amount * (1024 if DUCKDB_MEMORY_LIMIT.endswith("GB") else 1)
if _memory_mb > 2048:
    raise RuntimeError("QUERYLENS_DUCKDB_MEMORY_LIMIT cannot exceed 2048MB.")
_analysis_slots = threading.BoundedSemaphore(MAX_CONCURRENT_ANALYSES)


class AnalysisTimeout(TimeoutError):
    pass


class AnalysisBusy(RuntimeError):
    pass


def _execute_with_timeout(connection, sql: str, parameters=()):
    timed_out = Event()

    def interrupt():
        timed_out.set()
        try:
            connection.interrupt()
        except Exception:
            pass

    timer = Timer(QUERY_TIMEOUT_SECONDS, interrupt)
    timer.daemon = True
    timer.start()
    try:
        return connection.execute(sql, parameters)
    except Exception as exc:
        if timed_out.is_set():
            raise AnalysisTimeout("The analysis exceeded its execution time limit.") from exc
        raise
    finally:
        timer.cancel()
        if timed_out.is_set():
            timer.join(timeout=0.25)


def _ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def analyze(frame: pd.DataFrame, question: str) -> dict:
    if not _analysis_slots.acquire(blocking=False):
        raise AnalysisBusy("Analysis capacity is busy. Try again shortly.")
    try:
        return _analyze(frame, question)
    finally:
        _analysis_slots.release()


def _analyze(frame: pd.DataFrame, question: str) -> dict:
    raw_op = parse_intent(question, frame)
    if raw_op["kind"] == "clarification":
        return {"understood": False, "clarification": raw_op["message"], "operation": None, "sql": None, "query_parameters":[], "columns": [], "rows": [], "explanation": None, "chart": None}
    try:
        typed_op = validate_operation(raw_op, frame)
    except OperationValidationError as exc:
        return {"understood":False,"clarification":str(exc),"operation":None,"sql":None,"query_parameters":[],"columns":[],"rows":[],"explanation":None,"chart":None}
    op = typed_op.model_dump(mode="python", exclude_none=True)
    con = duckdb.connect(":memory:")
    con.execute(f"SET memory_limit='{DUCKDB_MEMORY_LIMIT}'")
    con.execute("SET threads=2")
    con.execute("SET enable_external_access=false")
    con.register("dataset", frame)
    try:
        where_sql, where_params = _where(op["filters"], op["filter_logic"])
        if op["kind"] in {"preview","filter"}:
            order = f" ORDER BY {_ident(op['sort_by'])} {op['sort_direction'].upper()} NULLS LAST" if op.get("sort_by") else ""
            sql = f"SELECT * FROM dataset{where_sql}{order} LIMIT ?"
            parameters = [*where_params,op["limit"]]
            rows = _records(_execute_with_timeout(con,sql,parameters).fetchdf())
            explanation = "Showing matching rows" if op["kind"]=="filter" or op["filters"] else "Showing dataset rows"
            if op.get("sort_by"): explanation += f", sorted by {op['sort_by']} {op['sort_direction']}"
            explanation += f" (maximum {op['limit']} rows)."
            return _result(op,sql,frame.columns.tolist(),rows,explanation,{"type":"table"},parameters)
        if op["kind"] == "correlation":
            columns = op["columns"]
            sql = "SELECT " + ", ".join(_ident(c) for c in columns) + f" FROM dataset{where_sql} LIMIT ?"
            parameters = [*where_params,50000]
            data = _execute_with_timeout(con, sql, parameters).df()
            method=op.get("correlation_method","pearson")
            sample_size=int(data.dropna().shape[0])
            corr = data.corr(method=method).round(4)
            rows = [{"column": i, **{str(c): _safe(v) for c, v in row.items()}} for i, row in corr.iterrows()]
            return _result(op, sql, ["column", *columns], rows, f"{method.title()} correlations calculated for {', '.join(columns)} using {sample_size} complete rows (up to 50,000 sampled rows). Correlation describes association, not causation; coefficients do not establish statistical significance.", {"type":"heatmap", "columns":columns, "values":corr.values.tolist(),"method":method,"sample_size":sample_size},parameters)
        if op["kind"] == "distribution":
            c = op["column"]
            base_where = f" WHERE {_ident(c)} IS NOT NULL" + (f" AND {where_sql[7:]}" if where_sql else "")
            sql = f"SELECT {_ident(c)} FROM dataset{base_where} LIMIT ?"
            parameters = [*where_params,50000]
            values = _execute_with_timeout(con, sql, parameters).fetchdf()[c]
            hist, edges = __import__("numpy").histogram(values, bins=min(20, max(1, int(len(values) ** .5)))) if len(values) else ([], [])
            rows = [{"bin": f"{edges[i]:.4g}–{edges[i+1]:.4g}", "count": int(hist[i])} for i in range(len(hist))]
            outliers = _outlier_count(values)
            explanation = f"Inspected {len(values)} non-missing values in {c}; the IQR rule flags {outliers} statistical outlier(s). This is an indicator, not proof of an error."
            return _result(op, sql, ["bin", "count"], rows, explanation, {"type":"bar", "x":"bin", "y":"count"},parameters)
        if op["kind"] == "timeseries":
            date_col, measure, period = op["date_column"], op["measure"], op["period"]
            if date_col not in frame.columns or not pd.api.types.is_datetime64_any_dtype(frame[date_col]):
                raise ValueError("Time series analysis requires a known date column.")
            if measure not in frame.columns or not pd.api.types.is_numeric_dtype(frame[measure]):
                raise ValueError("Time series analysis requires a known numeric measure.")
            if period not in {"month", "day"}: raise ValueError("Unsupported time interval.")
            base_where = f" WHERE {_ident(date_col)} IS NOT NULL" + (f" AND {where_sql[7:]}" if where_sql else "")
            sql = f"SELECT DATE_TRUNC('{period}', {_ident(date_col)}) AS period, SUM({_ident(measure)}) AS value, COUNT(*) AS row_count, COUNT({_ident(measure)}) AS non_null_count FROM dataset{base_where} GROUP BY 1 ORDER BY 1 LIMIT ?"
            parameters = [*where_params,MAX_RESULT_ROWS+1]
            totals = _execute_with_timeout(con, sql, parameters).fetchdf()
            if len(totals)>MAX_RESULT_ROWS:
                return _clarification("This time series contains more than 500 observed periods. Choose a coarser interval or filter the date range.")
            totals_by_period = {}
            for record in totals.to_dict(orient="records"):
                stamp = pd.Timestamp(record["period"])
                stamp = stamp.to_period("M").to_timestamp() if period == "month" else stamp.normalize()
                totals_by_period[stamp] = record
            if len(totals):
                start,end=pd.Timestamp(totals["period"].min()),pd.Timestamp(totals["period"].max())
                period_count=(end.year-start.year)*12+end.month-start.month+1 if period=="month" else (end.normalize()-start.normalize()).days+1
                if period_count>MAX_RESULT_ROWS:
                    return _clarification("The date range spans more than 500 calendar periods. Filter the date range before analyzing it.")
                calendar=pd.date_range(start,end,freq="MS" if period=="month" else "D")
            else:
                calendar=[]
            rows = []
            previous_record = None
            for index, stamp in enumerate(calendar):
                record = totals_by_period.get(stamp)
                if record is None:
                    rows.append({"period":stamp.strftime("%Y-%m" if period == "month" else "%Y-%m-%d"),"period_status":"missing_period","row_count":0,"non_null_count":0,"value":None,"growth_pct":None,"growth_status":"missing_period"})
                    previous_record = None
                    continue
                current_raw = record["value"]
                current = _safe(current_raw)
                growth, growth_status = None, "first_period" if index == 0 else "calculated"
                if current is None:
                    growth_status = "missing_current_value"
                elif previous_record is None:
                    growth_status = "first_period" if index == 0 else "missing_previous_period"
                elif _safe(previous_record["value"]) is None:
                    growth_status = "missing_previous_value"
                elif float(previous_record["value"]) == 0:
                    growth_status = "zero_baseline"
                else:
                    growth = float((float(current_raw) / float(previous_record["value"]) - 1) * 100)
                rows.append({"period":stamp.strftime("%Y-%m" if period == "month" else "%Y-%m-%d"),"period_status":"observed","row_count":int(record["row_count"]),"non_null_count":int(record["non_null_count"]),"value":current,"growth_pct":_safe(growth),"growth_status":growth_status})
                previous_record = record
            label = "monthly" if period == "month" else "daily"
            explanation = f"Summed non-missing {measure} values by {label}. Growth is (current total / immediately previous calendar-period total - 1) × 100; zero baselines and missing/current-null periods have no percentage. Missing calendar periods are shown with null totals, never zero-filled. Negative baselines use the same formula and may be hard to interpret as conventional growth."
            return _result(op, sql, ["period","period_status","row_count","non_null_count","value","growth_pct","growth_status"], rows, explanation, {"type":"line", "x":"period", "y":"growth_pct"},parameters)
        aggregation = op["aggregation"]
        group = op.get("group_by")
        measure = op.get("measure")
        if aggregation not in AGGREGATIONS or (group is not None and group not in frame.columns) or (measure and measure not in frame.columns):
            raise ValueError("The requested operation contains an unknown column or operation.")
        parameters = list(where_params)
        if group:
            val = f"{AGGREGATIONS[aggregation]}({_ident(measure)})" if measure and aggregation!="count" else "COUNT(*)"
            order_column = op.get("sort_by") or "value"
            if order_column not in {"value",group,measure}:
                return {"understood":False,"clarification":"Sort grouped results by the group or calculated value.","operation":None,"sql":None,"query_parameters":[],"columns":[],"rows":[],"explanation":None,"chart":None}
            order_sql = "value" if order_column=="value" or order_column==measure else _ident(group)
            sql = f"SELECT {_ident(group)}, {val} AS value FROM dataset{where_sql} GROUP BY {_ident(group)} ORDER BY {order_sql} {op['sort_direction'].upper()} NULLS LAST LIMIT ?"
            columns = [group, "value"]
        else:
            val = f"{AGGREGATIONS[aggregation]}({_ident(measure)})" if measure and aggregation!="count" else "COUNT(*)"
            sql = f"SELECT {val} AS value FROM dataset{where_sql}"
            columns = ["value"]
        if group: parameters.append(op["limit"])
        rows = _records(_execute_with_timeout(con, sql, parameters).fetchdf())
        measure_label = "rows" if aggregation=="count" else (measure or "rows")
        agg_label = {"sum":"total", "avg":"average", "min":"minimum", "max":"maximum", "count":"count"}[aggregation]
        explanation = (f"Calculated {agg_label} {measure_label}" + (f" by {group}" if group else " across the dataset") + f" from {len(frame):,} rows.")
        chart = {"type":"bar", "x":group, "y":"value"} if group else {"type":"metric", "value":"value"}
        return _result(op, sql, columns, rows, explanation, chart,parameters)
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

def _where(filters, logic):
    clauses, parameters = [], []
    mapping={"eq":"=","ne":"<>","gt":">","gte":">=","lt":"<","lte":"<="}
    for condition in filters:
        column=_ident(condition["column"]); operator=condition["operator"]
        if operator=="is_missing": clauses.append(f"{column} IS NULL")
        elif operator=="is_not_missing": clauses.append(f"{column} IS NOT NULL")
        elif operator=="between": clauses.append(f"{column} BETWEEN ? AND ?"); parameters.extend([condition["value"],condition["upper_value"]])
        elif operator in mapping:
            value=condition["value"]
            if isinstance(value,str) and operator in {"eq","ne"}:
                clauses.append(f"lower(CAST({column} AS VARCHAR)) {mapping[operator]} lower(?)")
            else:
                clauses.append(f"{column} {mapping[operator]} ?")
            parameters.append(value)
        elif operator=="contains": clauses.append(f"contains(lower(CAST({column} AS VARCHAR)), lower(?))"); parameters.append(condition["value"])
        elif operator=="starts_with": clauses.append(f"starts_with(lower(CAST({column} AS VARCHAR)), lower(?))"); parameters.append(condition["value"])
    return (f" WHERE ({f' {logic.upper()} '.join(clauses)})" if clauses else ""), parameters


def _json_safe(value):
    if isinstance(value, dict): return {str(k):_json_safe(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [_json_safe(v) for v in value]
    if hasattr(value,"isoformat"): return value.isoformat()
    if hasattr(value,"item"): return _json_safe(value.item())
    if isinstance(value,float) and not math.isfinite(value): return None
    return value


def _result(op, sql, columns, rows, explanation, chart, parameters=()):
    return {"understood":True, "clarification":None, "operation":_json_safe(op), "sql":sql, "query_parameters":_json_safe(list(parameters)), "columns":columns, "rows":_json_safe(rows), "explanation":explanation, "chart":_json_safe(chart)}

def _clarification(message):
    return {"understood":False,"clarification":message,"operation":None,"sql":None,"query_parameters":[],"columns":[],"rows":[],"explanation":None,"chart":None}

def _outlier_count(values):
    if len(values) < 4: return 0
    q1,q3=values.quantile([.25,.75]); span=q3-q1
    return 0 if span == 0 else int(((values<q1-1.5*span)|(values>q3+1.5*span)).sum())
