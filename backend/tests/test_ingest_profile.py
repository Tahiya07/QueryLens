import io
import pandas as pd
import pytest
from app.ingest import read_dataset
from app.profiling import profile

def test_csv_ingestion_normalizes_duplicate_headers():
    frame=read_dataset("sales.csv",b"product,product,revenue\nA,A,10\n")
    assert list(frame.columns)==["product","product_2","revenue"]
    assert frame.iloc[0]["revenue"]==10

def test_xlsx_ingestion():
    buffer=io.BytesIO()
    pd.DataFrame({"amount":[2,4]}).to_excel(buffer,index=False)
    assert read_dataset("book.xlsx",buffer.getvalue()).amount.sum()==6

def test_csv_date_inference():
    frame=read_dataset("events.csv",b"event_date,label\n2026-02-01,a\n2026-02-02,b\n")
    assert pd.api.types.is_datetime64_any_dtype(frame["event_date"])

@pytest.mark.parametrize("name,data",[("notes.txt",b"x"),("data.csv",b""),("broken.xlsx",b"not an xlsx")])
def test_rejects_unsupported_or_invalid_upload(name,data):
    with pytest.raises(ValueError): read_dataset(name,data)

def test_profile_known_missing_duplicates_and_outlier():
    frame=pd.DataFrame({"x":[1,2,2,3,100,None],"group":["a","a","a","b","b",None]})
    p=profile(frame)
    assert (p["rows"],p["columns"],p["duplicate_rows"])==(6,2,1)
    col=p["columns_profile"][0]
    assert col["missing"]==1 and col["unique"]==4
    assert col["statistics"]["mean"]==21.6 and col["statistics"]["outlier_count"]==1
