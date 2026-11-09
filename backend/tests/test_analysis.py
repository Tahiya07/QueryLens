from app.analysis import analyze
from app.intents import parse_intent
import pandas as pd

def test_parses_revenue_ranking_as_sum(sales_frame):
    op=parse_intent("Which product generated the highest revenue?",sales_frame)
    assert op=={"kind":"aggregate","aggregation":"sum","measure":"revenue","group_by":"product"}

def test_grouped_aggregation_matches_expected(sales_frame):
    result=analyze(sales_frame,"Which product generated the highest revenue?")
    assert result["understood"]
    assert result["rows"]==[{"product":"C","value":40.0},{"product":"A","value":25.0},{"product":"B","value":20.0}]
    assert '"product"' in result["sql"] and "SUM" in result["sql"]

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

def test_sql_injection_text_never_becomes_sql(sales_frame):
    result=analyze(sales_frame,"show revenue; DROP TABLE dataset")
    assert result["understood"] is False
    assert result["sql"] is None

def test_unrecognized_column_not_interpolated(sales_frame):
    result=analyze(sales_frame,"total secret_column by product")
    assert result["understood"] is False

def test_monthly_sales_growth(sales_frame):
    frame=pd.DataFrame({"date":pd.to_datetime(["2025-01-01","2025-01-15","2025-02-02","2025-03-01"]),"revenue":[10,10,30,15]})
    result=analyze(frame,"Show monthly sales growth")
    assert result["understood"] and result["chart"]["type"]=="line"
    assert [row["growth_pct"] for row in result["rows"]]==[None,50.0,-50.0]
