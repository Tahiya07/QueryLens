from app.analysis import analyze
from app.intents import parse_intent
import pandas as pd

def test_parses_revenue_ranking_as_sum(sales_frame):
    op=parse_intent("Which product generated the highest revenue?",sales_frame)
    assert {key: op[key] for key in ("kind", "aggregation", "measure", "group_by")} == {"kind":"aggregate","aggregation":"sum","measure":"revenue","group_by":"product"}
    op=parse_intent("Which revenue is highest by product?",sales_frame)
    assert op["group_by"]=="product" and op["measure"]=="revenue"

def test_grouped_aggregation_matches_expected(sales_frame):
    result=analyze(sales_frame,"Which product generated the highest revenue?")
    assert result["understood"]
    assert result["rows"]==[{"product":"C","value":40.0},{"product":"A","value":25.0},{"product":"B","value":20.0}]
    assert '"product"' in result["sql"] and "SUM" in result["sql"]

def test_grouped_count_counts_rows_even_when_measure_is_missing():
    frame=pd.DataFrame({"region":["East","East","West"],"revenue":[None,10,20]})
    result=analyze(frame,"Count rows by region")
    assert result["understood"] and result["rows"]==[{"region":"East","value":2},{"region":"West","value":1}]
    assert "COUNT(*)" in result["sql"]

def test_ambiguous_question_requests_clarification(sales_frame):
    result=analyze(sales_frame,"What is the best?")
    assert not result["understood"] and result["clarification"]

def test_distribution_and_outliers(sales_frame):
    result=analyze(sales_frame,"Show unusual values in revenue")
    assert result["chart"]["type"]=="bar"
    assert "IQR rule" in result["explanation"]

def test_correlation_is_evidence_only(sales_frame):
    result=analyze(sales_frame,"Correlation between revenue and units")
    assert result["understood"] and "not causation" in result["explanation"]

def test_spearman_reports_method_and_complete_case_sample_size():
    frame=pd.DataFrame({"x":[1,2,3,4,None],"y":[1,4,9,16,25]})
    result=analyze(frame,"Spearman correlation between x and y")
    assert result["understood"] and result["chart"]["method"]=="spearman"
    assert result["chart"]["sample_size"]==4 and result["rows"][0]["y"]==1.0
    assert "statistical significance" in result["explanation"]

def test_constant_column_correlation_serializes_null_coefficients():
    frame=pd.DataFrame({"x":[1,1,1,1],"y":[1,2,3,4]})
    result=analyze(frame,"Correlation between x and y")
    assert result["understood"] and result["rows"][0]["x"] is None
    import json
    json.dumps(result,allow_nan=False)

def test_sql_injection_text_never_becomes_sql(sales_frame):
    result=analyze(sales_frame,"show revenue; DROP TABLE dataset")
    assert result["understood"] is False
    assert result["sql"] is None

def test_filter_values_are_bound_even_when_they_look_like_sql(sales_frame):
    payload="East' OR 'x'='x"
    result=analyze(sales_frame,f"Show rows where region equals {payload}")
    assert result["understood"] and result["rows"]==[]
    assert payload not in result["sql"] and payload.lower() in result["query_parameters"]

def test_filter_equals_and_greater_than_are_parameterized(sales_frame):
    result=analyze(sales_frame,"Show sales where region equals East and revenue greater than 10")
    assert result["understood"]
    assert result["rows"]==[{"product":"B","revenue":11,"region":"East","units":1}]
    bare_is=analyze(sales_frame,"Show rows where region is East")
    assert bare_is["understood"] and len(bare_is["rows"])==3

def test_filter_and_or_missing_contains_and_between(sales_frame):
    east=analyze(sales_frame,"Show rows where region equals East or region equals West")
    assert east["understood"] and len(east["rows"])==5
    frame=sales_frame.copy(); frame.loc[0,"region"]=None
    missing=analyze(frame,"Show rows where region is missing")
    assert missing["understood"] and len(missing["rows"])==1
    contains=analyze(sales_frame,"Show rows where product contains A")
    assert contains["understood"] and len(contains["rows"])==2
    between=analyze(sales_frame,"Show revenue between 10 and 20")
    assert between["understood"] and [r["revenue"] for r in between["rows"]]==[10,15,11]

def test_grouped_top_and_bottom_sorting(sales_frame):
    top=analyze(sales_frame,"Show the top 2 products by revenue")
    assert top["understood"] and top["rows"]==[{"product":"C","value":40.0},{"product":"A","value":25.0}]
    bottom=analyze(sales_frame,"Find the two regions with the lowest average revenue")
    assert bottom["understood"] and bottom["rows"][0]["value"] < bottom["rows"][1]["value"]

def test_filter_validation_rejects_unknown_columns_and_bad_types(sales_frame):
    unknown=analyze(sales_frame,"Show rows where secret equals x")
    assert not unknown["understood"] and unknown["sql"] is None
    invalid=analyze(sales_frame,"Show rows where revenue greater than many")
    assert not invalid["understood"] and "numeric" in invalid["clarification"]

def test_text_sorting_and_filter_sort_direction(sales_frame):
    result=analyze(sales_frame,"Sort rows by product ascending")
    assert result["understood"]
    assert [row["product"] for row in result["rows"]]==sorted(row["product"] for row in sales_frame.to_dict("records"))

def test_date_filter_is_typed_and_parameterized():
    frame=pd.DataFrame({"date":pd.to_datetime(["2025-01-01","2025-02-01","2025-03-01"]),"revenue":[1,2,3]})
    result=analyze(frame,"Show rows where date between 2025-02-01 and 2025-03-01")
    assert result["understood"] and [r["revenue"] for r in result["rows"]]==[2,3]

def test_between_rejects_inverted_bounds_and_boolean_ordering():
    numeric=pd.DataFrame({"amount":[1,2,3]})
    inverted=analyze(numeric,"Show rows where amount between 3 and 1")
    assert not inverted["understood"] and "lower bound" in inverted["clarification"]
    boolean=pd.DataFrame({"active":[True,False]})
    ordered=analyze(boolean,"Show rows where active greater than true")
    assert not ordered["understood"] and "boolean" in ordered["clarification"]

def test_unrecognized_column_not_interpolated(sales_frame):
    result=analyze(sales_frame,"total secret_column by product")
    assert result["understood"] is False

def test_monthly_sales_growth(sales_frame):
    frame=pd.DataFrame({"date":pd.to_datetime(["2025-01-01","2025-01-15","2025-02-02","2025-03-01"]),"revenue":[10,10,30,15]})
    result=analyze(frame,"Show monthly sales growth")
    assert result["understood"] and result["chart"]["type"]=="line"
    assert [row["growth_pct"] for row in result["rows"]]==[None,50.0,-50.0]

def test_time_series_marks_missing_periods_and_null_zero_negative_baselines():
    frame=pd.DataFrame({
        "date":pd.to_datetime(["2025-01-05","2025-03-02","2025-04-01","2025-05-01","2025-06-01","2025-07-01"]),
        "revenue":[0,5,-5,0,None,7],
    })
    rows=analyze(frame,"Show monthly revenue growth")["rows"]
    assert [row["period"] for row in rows]==["2025-01","2025-02","2025-03","2025-04","2025-05","2025-06","2025-07"]
    assert rows[1]["period_status"]=="missing_period" and rows[1]["value"] is None
    assert rows[2]["growth_status"]=="missing_previous_period"
    assert rows[3]["growth_status"]=="calculated" and rows[3]["growth_pct"]==-200.0
    assert rows[4]["growth_pct"]==-100.0
    assert rows[5]["growth_status"]=="missing_current_value" and rows[5]["value"] is None
    assert rows[6]["growth_status"]=="missing_previous_value"

def test_zero_baseline_has_no_fabricated_growth():
    frame=pd.DataFrame({"date":pd.to_datetime(["2025-01-01","2025-02-01"]),"revenue":[0,5]})
    rows=analyze(frame,"Show monthly revenue growth")["rows"]
    assert rows[1]["growth_pct"] is None and rows[1]["growth_status"]=="zero_baseline"

def test_time_series_calendar_expansion_is_bounded():
    frame=pd.DataFrame({"date":pd.to_datetime(["2020-01-01","2022-01-01"]),"revenue":[1,2]})
    result=analyze(frame,"Show daily revenue growth")
    assert not result["understood"] and "500 calendar periods" in result["clarification"]
