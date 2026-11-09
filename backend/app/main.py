from __future__ import annotations
import hashlib, io, json, logging, os, uuid
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
import pandas as pd
import jwt
from sqlalchemy.exc import IntegrityError
from . import security
from .analysis import AnalysisBusy, AnalysisTimeout, analyze
from .ingest import read_dataset
from .exports import safe_spreadsheet_frame
from .profiling import profile
from .rate_limit import LocalRateLimiter
from .schemas import AnalysisRequest, AnalysisResult, DatasetDetail, DatasetSummary, ExportRequest, HealthResponse, TokenResponse

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("querylens")
app = FastAPI(title="QueryLens API", version="0.1.0", description="Local-first, deterministic data analysis API")
app.state.rate_limiter = LocalRateLimiter()
origins = [x.strip() for x in os.getenv("QUERYLENS_CORS_ORIGINS", "http://localhost:3000").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["GET","POST","DELETE"], allow_headers=["Authorization","Content-Type"])

@app.middleware("http")
async def apply_local_rate_limits(request, call_next):
    path = request.url.path
    scope = None
    if path == "/api/auth/register": scope = "register"
    elif path == "/api/auth/login": scope = "login"
    elif path.startswith("/api/datasets/") and path.endswith("/analyze"): scope = "analysis"
    elif path.startswith("/api/datasets/") and path.endswith("/export"): scope = "export"
    if scope:
        identity = request.client.host if request.client else "unknown"
        allowed, retry_after = request.app.state.rate_limiter.consume(scope, identity)
        if not allowed:
            return Response("{\"detail\":\"Too many requests. Try again later.\"}", status_code=429, media_type="application/json", headers={"Retry-After":str(retry_after)})
    return await call_next(request)

security.initialize()

@app.exception_handler(AnalysisTimeout)
async def analysis_timeout_handler(_request, exc: AnalysisTimeout):
    return JSONResponse(status_code=504, content={"detail":str(exc)})

@app.exception_handler(AnalysisBusy)
async def analysis_busy_handler(_request, exc: AnalysisBusy):
    return JSONResponse(status_code=503, content={"detail":str(exc)}, headers={"Retry-After":"1"})

class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)

    @field_validator("password")
    @classmethod
    def bcrypt_byte_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes.")
        return value

def current_user(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sign in to continue.")
    try: return security.decode_token(authorization[7:])
    except (jwt.InvalidTokenError, KeyError): raise HTTPException(401, "Your session is invalid or expired.")

def owned_dataset(dataset_id: str, user_id: str):
    with security.connect() as db:
        row = db.execute("SELECT * FROM datasets WHERE id=? AND owner_id=?", (dataset_id, user_id)).fetchone()
    if not row: raise HTTPException(404, "Dataset not found.")
    return row

def load_frame(dataset):
    path = security.DATA_DIR / "datasets" / dataset["storage_name"]
    try: return pd.read_pickle(path)
    except Exception as exc:
        logger.exception("Dataset storage could not be read")
        raise HTTPException(500, "Dataset could not be loaded.") from exc

@app.get("/api/health", response_model=HealthResponse)
def health(): return {"status":"ok", "llm_enabled":False}

@app.post("/api/auth/register", response_model=TokenResponse)
def register(credentials: Credentials):
    try: user = security.create_user(credentials.email, credentials.password)
    except IntegrityError as exc: raise HTTPException(409, "An account with this email already exists.") from exc
    return {"token":security.token_for(user["id"]), "email":user["email"]}

@app.post("/api/auth/login", response_model=TokenResponse)
def login(credentials: Credentials):
    user = security.authenticate(credentials.email, credentials.password)
    if not user: raise HTTPException(401, "Email or password is incorrect.")
    return {"token":security.token_for(user["id"]), "email":user["email"]}

@app.get("/api/datasets", response_model=list[DatasetSummary])
def list_datasets(user_id: str=Depends(current_user)):
    with security.connect() as db:
        rows=db.execute("SELECT id,name,rows,columns_count,created_at FROM datasets WHERE owner_id=? ORDER BY created_at DESC",(user_id,)).fetchall()
    return [{"id":r["id"],"name":r["name"],"rows":r["rows"],"columns":r["columns_count"],"created_at":r["created_at"]} for r in rows]

@app.post("/api/datasets", response_model=DatasetSummary)
async def upload_dataset(file: UploadFile=File(...), user_id: str=Depends(current_user)):
    data=await file.read(25*1024*1024+1)
    try: frame=read_dataset(file.filename or "", data)
    except ValueError as exc: raise HTTPException(400, str(exc))
    dataset_id=str(uuid.uuid4()); storage=f"{dataset_id}.pkl"; now=datetime.now(timezone.utc).isoformat()
    folder=security.DATA_DIR/"datasets"; folder.mkdir(exist_ok=True)
    # Use generated storage names; never derive a path from the submitted filename.
    frame.to_pickle(folder/storage)
    try:
        with security.connect() as db:
            db.execute("INSERT INTO datasets VALUES (?,?,?,?,?,?,?)", (dataset_id,user_id,Path(file.filename or "dataset.csv").name,storage,len(frame),len(frame.columns),now))
    except Exception:
        (folder/storage).unlink(missing_ok=True); raise
    logger.info("dataset_uploaded", extra={"dataset_id":dataset_id,"rows":len(frame),"columns":len(frame.columns)})
    return {"id":dataset_id,"name":Path(file.filename or "dataset.csv").name,"rows":len(frame),"columns":len(frame.columns),"created_at":now}

@app.get("/api/datasets/{dataset_id}", response_model=DatasetDetail)
def dataset_detail(dataset_id: str, user_id: str=Depends(current_user)):
    ds=owned_dataset(dataset_id,user_id); frame=load_frame(ds)
    return {"id":ds["id"],"name":ds["name"],"rows":ds["rows"],"columns":ds["columns_count"],"created_at":ds["created_at"],"profile":profile(frame),"preview":frame.head(100).where(pd.notnull(frame.head(100)),None).to_dict(orient="records")}

@app.delete("/api/datasets/{dataset_id}")
def delete_dataset(dataset_id: str, user_id: str=Depends(current_user)):
    ds=owned_dataset(dataset_id,user_id)
    with security.connect() as db: db.execute("DELETE FROM datasets WHERE id=? AND owner_id=?",(dataset_id,user_id))
    (security.DATA_DIR/"datasets"/ds["storage_name"]).unlink(missing_ok=True)
    return {"deleted":True}

@app.post("/api/datasets/{dataset_id}/analyze", response_model=AnalysisResult)
def run_analysis(dataset_id: str, request: AnalysisRequest, user_id: str=Depends(current_user)):
    ds=owned_dataset(dataset_id,user_id); path=security.DATA_DIR/"datasets"/ds["storage_name"]; frame=load_frame(ds); result=analyze(frame,request.question)
    if result["understood"]:
        fingerprint=hashlib.sha256(path.read_bytes()).hexdigest()
        timestamp=datetime.now(timezone.utc).isoformat()
        result["provenance"]={"dataset_id":dataset_id,"dataset_fingerprint":fingerprint,"original_question":request.question,"operation":result.get("operation"),"selected_columns":result.get("columns",[]),"parameters":result.get("query_parameters",[]),"executed_at":timestamp,"result_rows":len(result.get("rows",[])),"limitations":["Results are bounded by the configured analysis row and resource limits.","Associations do not establish causation."]}
        with security.connect() as db:
            db.execute("INSERT INTO analyses(dataset_id,owner_id,question,result_json,created_at) VALUES(?,?,?,?,?)",(dataset_id,user_id,request.question,json.dumps(result,allow_nan=False),timestamp))
    return result

@app.get("/api/datasets/{dataset_id}/history")
def query_history(dataset_id: str, user_id: str=Depends(current_user)):
    owned_dataset(dataset_id,user_id)
    with security.connect() as db: rows=db.execute("SELECT id,question,created_at FROM analyses WHERE dataset_id=? AND owner_id=? ORDER BY id DESC LIMIT 50",(dataset_id,user_id)).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/datasets/{dataset_id}/export")
def export(dataset_id: str, request: ExportRequest, user_id: str=Depends(current_user)):
    ds=owned_dataset(dataset_id,user_id); result=analyze(load_frame(ds),request.question)
    if not result["understood"]: raise HTTPException(422,result["clarification"])
    df=pd.DataFrame(result["rows"])
    if request.format=="csv":
        return StreamingResponse(iter([safe_spreadsheet_frame(df).to_csv(index=False)]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=querylens-results.csv"})
    if request.format=="xlsx":
        safe_df = safe_spreadsheet_frame(df)
        output=io.BytesIO()
        with pd.ExcelWriter(output,engine="openpyxl") as writer:
            safe_df.to_excel(writer,index=False,sheet_name="Results")
            safe_spreadsheet_frame(pd.DataFrame([{"Dataset":ds["name"],"Question":request.question,"Calculation":result["explanation"],"Operation":json.dumps(result["operation"])}])).to_excel(writer,index=False,sheet_name="Analysis")
            chart_spec=result.get("chart") or {}
            if chart_spec.get("type") in {"bar","line"} and len(df):
                from openpyxl.chart import BarChart, LineChart, Reference
                chart_type=LineChart if chart_spec["type"]=="line" else BarChart
                chart=chart_type(); chart.title=result["explanation"][:80]; chart.style=10
                y_name=chart_spec.get("y")
                if y_name in safe_df.columns:
                    y_index=list(safe_df.columns).index(y_name)+1
                    chart.add_data(Reference(writer.sheets["Results"],min_col=y_index,min_row=1,max_row=min(len(safe_df),100)+1),titles_from_data=True)
                    if chart_spec.get("x") in safe_df.columns:
                        x_index=list(safe_df.columns).index(chart_spec["x"])+1
                        chart.set_categories(Reference(writer.sheets["Results"],min_col=x_index,min_row=2,max_row=min(len(safe_df),100)+1))
                    writer.sheets["Results"].add_chart(chart,"E2")
        return Response(output.getvalue(),media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":"attachment; filename=querylens-report.xlsx"})
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    output=io.BytesIO(); pdf=canvas.Canvas(output,pagesize=letter); pdf.setTitle("QueryLens Analysis Report")
    pdf.setFont("Helvetica-Bold",18); pdf.drawString(48,745,"QueryLens Analysis Report"); pdf.setFont("Helvetica",10)
    details=[f"Dataset: {ds['name']}",f"Question: {request.question}",f"Calculation: {result['explanation']}",f"Operation: {json.dumps(result['operation'])}"]
    y=718
    for line in details:
        for part in [line[i:i+100] for i in range(0,len(line),100)]: pdf.drawString(48,y,part); y-=14
    chart_spec=result.get("chart") or {}
    if chart_spec.get("type") in {"bar","line"} and len(df):
        pdf.setFont("Helvetica-Bold",11); pdf.drawString(48,y-5,"Results chart")
        left, bottom, width, height = 62, 435, 480, 150
        pdf.setStrokeColorRGB(.35,.39,.43); pdf.line(left,bottom,left+width,bottom); pdf.line(left,bottom,left,bottom+height)
        if chart_spec["type"]=="bar":
            labels=df[chart_spec["x"]].astype(str).tolist()[:12]; values=pd.to_numeric(df[chart_spec["y"]],errors="coerce").fillna(0).tolist()[:12]
            top=max([abs(v) for v in values] or [1]) or 1; step=width/max(1,len(values)); bar=step*.62
            for i,(label,value) in enumerate(zip(labels,values)):
                x=left+i*step+(step-bar)/2; bh=max(1,abs(value)/top*height)
                pdf.setFillColorRGB(.61,.91,.76); pdf.rect(x,bottom,bar,bh,fill=1,stroke=0)
                pdf.setFillColorRGB(.83,.85,.88); pdf.setFont("Helvetica",7); pdf.drawCentredString(x+bar/2,bottom-12,label[:16])
        else:
            values=pd.to_numeric(df[chart_spec["y"]],errors="coerce").tolist(); valid=[(i,v) for i,v in enumerate(values) if pd.notna(v)]
            if valid:
                low=min(v for _,v in valid); high=max(v for _,v in valid); span=high-low or 1
                points=[(left+i*width/max(1,len(values)-1),bottom+(v-low)/span*height) for i,v in valid]
                pdf.setStrokeColorRGB(.61,.91,.76); pdf.setLineWidth(2)
                for a,b in zip(points,points[1:]): pdf.line(a[0],a[1],b[0],b[1])
                pdf.setFillColorRGB(.61,.91,.76)
                for x,y_point in points: pdf.circle(x,y_point,3,fill=1,stroke=0)
    pdf.setFont("Helvetica-Bold",11); pdf.setFillColorRGB(.12,.14,.17); pdf.drawString(48,405,"Result rows")
    pdf.setFont("Courier",8); y=388
    for line in df.head(35).to_string(index=False).splitlines():
        for part in [line[i:i+105] for i in range(0,len(line),105)]:
            if y<45: pdf.showPage(); pdf.setFont("Courier",8); y=750
            pdf.drawString(48,y,part); y-=12
    pdf.save(); return Response(output.getvalue(),media_type="application/pdf",headers={"Content-Disposition":"attachment; filename=querylens-report.pdf"})
