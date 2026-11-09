"""Conservative natural-language parser. It returns operation descriptions, never executable SQL."""
from __future__ import annotations
import re
from typing import Any
import pandas as pd

_NUMBER_WORDS={"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10,"twenty":20,"fifty":50,"hundred":100}
_OPERATORS=(
    (r"^is\s+not\s+(?:missing|null)\b|^is\s+not\s+missing\b","is_not_missing"),
    (r"^is\s+(?:missing|null)\b|^is\s+missing\b","is_missing"),
    (r"^greater\s+than\s+or\s+equal(?:\s+to)?\b|^at\s+least\b|^>=\s*","gte"),
    (r"^less\s+than\s+or\s+equal(?:\s+to)?\b|^at\s+most\b|^<=\s*","lte"),
    (r"^not\s+equal(?:s)?(?:\s+to)?\b|^does\s+not\s+equal\b|^!=\s*","ne"),
    (r"^greater\s+than\b|^more\s+than\b|^above\b|^exceeds\b|^>\s*","gt"),
    (r"^less\s+than\b|^below\b|^under\b|^<\s*","lt"),
    (r"^starts?\s+with\b","starts_with"),
    (r"^contains?\b","contains"),
    (r"^between\b","between"),
    (r"^equals?\b|^equal\s+to\b|^is\b|^=\s*","eq"),
)


def _column_pattern(column: str) -> str:
    return r"[\s_-]+".join(re.escape(part) for part in re.split(r"[_\s-]+",column.lower()) if part)


def _column(text: str, columns: list[str]) -> str | None:
    lower = text.lower()
    matches=[]
    for column in columns:
        label=column.lower()
        forms={label,label+"s"}
        if label.endswith("y") and len(label)>1: forms.add(label[:-1]+"ies")
        if label.endswith("s"): forms.add(label+"es")
        if any(re.search(rf"(?<!\w){_column_pattern(form)}(?!\w)",lower) for form in forms): matches.append(column)
    return max(matches, key=len) if matches else None


def _group_column(q: str, columns: list[str]) -> str | None:
    candidates=[]
    numeric=[]
    # Entities named by top/bottom N and "which product ..." are clear group labels.
    for column in columns:
        c=_column_pattern(column)
        patterns=(rf"\bwhich\s+{c}\b",rf"\b(?:top|bottom)\s+(?:\d+|[a-z]+)\s+{c}\b",rf"\b{c}\s+(?:generated|contributed|made|sold|has|had)\b")
        if any(re.search(pattern,q) for pattern in patterns): candidates.append(column)
    if candidates:
        relationship=list(re.finditer(r"\b(?:by|across|per)\s+",q))
        if relationship:
            for context in relationship:
                related=_column(q[context.end():],columns)
                if related:return related
        return max(candidates,key=len)
    ranked=re.search(r"\b(?:top|bottom|the\s+)?(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(.+?)\s+(?:with|by)\b",q)
    if ranked:
        found=_column(ranked.group(1),columns)
        if found: return found
    # In "top 10 products by revenue", the entity occurs before the measure.
    top_match=re.search(r"\b(?:top|bottom)\s+(?:\d+|[a-z]+)\s+(.+?)\s+by\b",q)
    if top_match:
        found=_column(top_match.group(1),columns)
        if found: return found
    # Average/total ... by region, or compare ... across segment.
    contexts=list(re.finditer(r"\b(?:by|across|per)\s+",q))
    for context in contexts:
        tail=q[context.end():]
        found=_column(tail,columns)
        if found:
            # A measure following "top N ... by" is not the group.
            if re.search(r"\b(?:top|bottom)\s+(?:\d+|[a-z]+)\s+.+$",q[:context.start()]):
                continue
            return found
    return None


def _operator(suffix: str):
    leading = len(suffix) - len(suffix.lstrip())
    suffix = suffix.lstrip()
    for pattern, operator in _OPERATORS:
        match=re.match(pattern,suffix,re.I)
        if match:
            if operator=="eq" and suffix.startswith("is") and re.match(r"is\s+(highest|lowest|largest|smallest|best|worst|maximum|minimum)\b",suffix):
                continue
            return operator,leading+match.end()
    return None


def _filter_candidates(q: str, columns: list[str]):
    candidates=[]
    for column in columns:
        pattern=rf"(?<!\w){_column_pattern(column)}(?!\w)"
        for match in re.finditer(pattern,q):
            parsed=_operator(q[match.end():])
            if parsed:
                operator,offset=parsed
                candidates.append({"column":column,"start":match.start(),"value_start":match.end()+offset,"operator":operator})
    candidates.sort(key=lambda item:(item["start"],-len(item["column"])))
    filtered=[]
    for candidate in candidates:
        if filtered and candidate["start"] < filtered[-1]["start"]+len(filtered[-1]["column"]):
            continue
        filtered.append(candidate)
    return filtered


def _parse_filters(q: str, columns: list[str]):
    candidates=_filter_candidates(q,columns)
    if not candidates:
        signal=bool(re.search(r"\b(where|equals?|greater|less|above|below|contains|starts? with|missing|null|at least|at most)\b|[<>=]",q) or ("between" in q and not re.search(r"\b(?:correlation|relationship)\s+between\b",q)))
        return [],"and",("I couldn't resolve a filter to known columns and supported operators." if signal else None)
    conditions=[]; connectors=[]
    for index,candidate in enumerate(candidates):
        end=candidates[index+1]["start"] if index+1<len(candidates) else len(q)
        segment=q[candidate["value_start"]:end]
        if index+1==len(candidates) and re.search(r"\b(?:and|or)\b.+\b(?:equals?|greater|less|contains|starts?\s+with|between|missing|null)\b",segment):
            return [],"and","I couldn't resolve every filter condition to a known column."
        if index+1<len(candidates):
            between_connector=re.search(r"\s+(and|or)\s*$",segment)
            if between_connector:
                connectors.append(between_connector.group(1))
                segment=segment[:between_connector.start()]
        operator=candidate["operator"]
        condition={"column":candidate["column"],"operator":operator}
        if operator in {"is_missing","is_not_missing"}:
            conditions.append(condition); continue
        segment=re.sub(r"\s+(?:by|grouped\s+by)\s+.*$","",segment).strip(" \t\r\n.,;!?'")
        if operator=="between":
            bounds=re.split(r"\s+and\s+",segment,maxsplit=1)
            if len(bounds)!=2:
                return [],"and","A between filter needs a lower and upper bound joined by 'and'."
            condition["value"],condition["upper_value"]=bounds[0].strip(),bounds[1].strip()
        else:
            condition["value"]=segment
        if not condition.get("value"):
            return [],"and",f"Add a comparison value for {candidate['column']}."
        conditions.append(condition)
    if len(set(connectors))>1:
        return [],"and","Use one explicit AND or OR rule per filter request; mixed logic needs parentheses."
    logic=connectors[0] if connectors else "and"
    return conditions,logic,None


def _limit(q: str, default=100) -> int:
    match=re.search(r"\b(?:top|bottom|first|last)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|twenty|fifty|hundred)\b",q)
    if not match: return default
    raw=match.group(1)
    number=int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
    return max(1,min(500,number))


def parse_intent(question: str, frame: pd.DataFrame) -> dict[str, Any]:
    q=question.strip().lower()
    if re.search(r"[;]|\b(drop|delete|insert|update|alter|attach|detach|pragma|copy|read_csv|read_parquet)\b",q):
        return _unclear("That request contains unsupported command syntax. Ask for an analytical result in plain language.")
    cols=[str(c) for c in frame.columns]
    numeric=frame.select_dtypes(include="number").columns.tolist()
    dates=frame.select_dtypes(include=["datetime","datetimetz"]).columns.tolist()
    measures=[c for c in numeric if re.search(rf"(?<!\w){re.escape(str(c).lower())}(?!\w)",q)]
    measure=measures[0] if measures else (str(numeric[0]) if len(numeric)==1 else None)
    group=_group_column(q,cols)
    filters,filter_logic,filter_error=_parse_filters(q,cols)
    if filter_error:
        return _unclear(filter_error)
    if any(w in q for w in ("growth","over time","month by month")):
        date_col=_column(q,dates) or (str(dates[0]) if len(dates)==1 else None)
        if not date_col: return _unclear("Which date column should I use for the time series?")
        if not measure:
            preferred=[str(c) for c in numeric if re.search(r"revenue|sales|amount|total",str(c),re.I)]
            measure=preferred[0] if len(preferred)==1 else None
        if not measure: return _unclear("Which numeric measure should I compare over time?")
        period="month" if any(w in q for w in ("month","monthly")) else "day"
        return {"kind":"timeseries","date_column":date_col,"measure":measure,"period":period,"filters":filters,"filter_logic":filter_logic}

    if any(word in q for word in ("correlat","relationship between")):
        if len(measures)>=2: named=measures
        else: named=[str(c) for c in numeric if re.search(rf"(?<!\w){re.escape(str(c).lower())}(?!\w)",q)]
        if len(named)>=2:
            return {"kind":"correlation","columns":list(dict.fromkeys(named))[:8],"correlation_method":"spearman" if "spearman" in q else "pearson","filters":filters,"filter_logic":filter_logic}
        return _unclear("Name at least two numeric columns to compare correlation.")
    if any(w in q for w in ("unusual","outlier","anomal","distribution","histogram")):
        target=_column(q,[str(c) for c in numeric]) if numeric else None
        if not target and len(numeric)==1: target=str(numeric[0])
        if target: return {"kind":"distribution","column":target,"filters":filters,"filter_logic":filter_logic}
        return _unclear("Which numeric column should I inspect for unusual values?")

    is_average=any(w in q for w in ("average","mean"))
    ascending=any(w in q for w in ("lowest","least","minimum","smallest","bottom","ascending"))
    explicit_sort=bool(re.search(r"\b(sort|order)\b",q))
    sort_by=_column(q,cols) if explicit_sort else None
    limit=_limit(q)
    explicit_aggregate=bool(re.search(r"\b(average|mean|total|sum|count|highest|most|maximum|largest|top|best|lowest|least|minimum|smallest|bottom)\b",q))
    if filters and not explicit_aggregate:
        return {"kind":"filter","filters":filters,"filter_logic":filter_logic,"limit":limit,"sort_by":sort_by,"sort_direction":"asc" if ascending else "desc"}
    if is_average: agg="avg"
    elif any(w in q for w in ("count","how many")): agg="count"
    elif any(w in q for w in ("highest","most","maximum","largest","top","best")):
        agg="sum" if group and measure and any(w in q for w in ("revenue","sales","generated","total")) else "max"
        if is_average: agg="avg"
    elif any(w in q for w in ("lowest","least","minimum","smallest","bottom")):
        agg="avg" if is_average else "min"
    elif any(w in q for w in ("total","sum","revenue","sales")): agg="sum"
    else: agg=None

    if explicit_sort and not agg:
        if not sort_by:
            return _unclear("Which known column should I sort by?")
        return {"kind":"preview","limit":limit,"sort_by":sort_by,"sort_direction":"asc" if ascending else "desc","filters":filters,"filter_logic":filter_logic}
    if agg:
        if agg!="count" and not measure and len(numeric)>1: return _unclear("Which numeric column should I calculate?")
        measure_value=None if agg=="count" else (measure or (str(numeric[0]) if numeric else None))
        return {"kind":"aggregate","aggregation":agg,"measure":measure_value,"group_by":group,"filters":filters,"filter_logic":filter_logic,"sort_direction":"asc" if ascending else "desc","limit":limit}
    if filters:
        return {"kind":"filter","filters":filters,"filter_logic":filter_logic,"limit":limit,"sort_by":sort_by,"sort_direction":"asc" if ascending else "desc"}
    if any(w in q for w in ("show","preview","list","display")):
        return {"kind":"preview","limit":limit}
    return _unclear("I couldn’t map that question to a supported operation. Try an aggregation, filter, sort, distribution, correlation, or preview.")


def _unclear(message): return {"kind":"clarification","message":message}
