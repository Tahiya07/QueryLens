import io
from openpyxl import load_workbook
from fastapi.testclient import TestClient
from app.main import app

client=TestClient(app)

def register(email):
    response=client.post("/api/auth/register",json={"email":email,"password":"correct-horse-battery"})
    assert response.status_code==200
    return {"Authorization":"Bearer "+response.json()["token"]}

def test_auth_upload_analyze_export_and_isolation():
    headers=register("first@example.com")
    second=register("second@example.com")
    response=client.post("/api/datasets",headers=headers,files={"file":("sample.csv",b"product,revenue\nA,10\nA,15\nB,20\n","text/csv")})
    assert response.status_code==200
    dataset=response.json()
    result=client.post(f"/api/datasets/{dataset['id']}/analyze",headers=headers,json={"question":"Which product generated the highest revenue?"})
    assert result.status_code==200 and result.json()["rows"][0]["value"]==25
    provenance=result.json()["provenance"]
    assert provenance["dataset_id"]==dataset["id"] and len(provenance["dataset_fingerprint"])==64
    assert provenance["original_question"]=="Which product generated the highest revenue?"
    history=client.get(f"/api/datasets/{dataset['id']}/history",headers=headers)
    assert history.status_code==200 and history.json()[0]["question"]==provenance["original_question"]
    denied=client.get(f"/api/datasets/{dataset['id']}",headers=second)
    assert denied.status_code==404
    export=client.post(f"/api/datasets/{dataset['id']}/export",headers=headers,json={"question":"Which product generated the highest revenue?","format":"csv"})
    assert export.status_code==200 and "product,value" in export.text
    workbook=client.post(f"/api/datasets/{dataset['id']}/export",headers=headers,json={"question":"Which product generated the highest revenue?","format":"xlsx"})
    pdf=client.post(f"/api/datasets/{dataset['id']}/export",headers=headers,json={"question":"Which product generated the highest revenue?","format":"pdf"})
    assert workbook.status_code==200 and workbook.content.startswith(b"PK")
    assert len(load_workbook(io.BytesIO(workbook.content))["Results"]._charts)==1
    assert pdf.status_code==200 and pdf.content.startswith(b"%PDF")

def test_invalid_upload_returns_safe_error():
    headers=register("third@example.com")
    response=client.post("/api/datasets",headers=headers,files={"file":("evil.exe",b"x","application/octet-stream")})
    assert response.status_code==400 and "CSV" in response.json()["detail"]

def test_auth_required():
    assert client.get("/api/datasets").status_code==401

def test_auth_rate_limit_and_password_byte_limit():
    from app.main import app
    app.state.rate_limiter.policies["login"]=(1,60)
    first=client.post("/api/auth/login",json={"email":"unknown@example.com","password":"correct-horse-battery"})
    second=client.post("/api/auth/login",json={"email":"unknown@example.com","password":"correct-horse-battery"})
    assert first.status_code==401 and second.status_code==429
    too_long=client.post("/api/auth/register",json={"email":"long@example.com","password":"é"*40})
    assert too_long.status_code==422

def test_spreadsheet_exports_escape_formula_like_text():
    headers=register("formula@example.com")
    upload=client.post("/api/datasets",headers=headers,files={"file":("formula.csv",b"product,revenue\n=1+1,5\nNormal,-3\n","text/csv")})
    dataset_id=upload.json()["id"]
    payload={"question":"Which product generated the highest revenue?","format":"xlsx"}
    workbook=client.post(f"/api/datasets/{dataset_id}/export",headers=headers,json=payload)
    sheet=load_workbook(io.BytesIO(workbook.content),data_only=False)["Results"]
    assert sheet["A2"].value=="'=1+1" and sheet["A2"].data_type=="s"
    assert sheet["B2"].value==5
    payload["format"]="csv"
    csv_result=client.post(f"/api/datasets/{dataset_id}/export",headers=headers,json=payload)
    assert "'=1+1" in csv_result.text
