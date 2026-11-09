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
