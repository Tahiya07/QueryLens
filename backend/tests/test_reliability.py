from datetime import datetime, timedelta, timezone
import jwt
import pandas as pd
import pytest

from app.analysis import AnalysisBusy, AnalysisTimeout, _analysis_slots, _execute_with_timeout, analyze
from app.exports import safe_spreadsheet_frame
from app.rate_limit import LocalRateLimiter
from app.security import JWT_SECRET, decode_token, resolve_jwt_secret, token_for


def test_production_requires_strong_non_default_signing_secret():
    with pytest.raises(RuntimeError, match="Production requires"):
        resolve_jwt_secret("production", None)
    with pytest.raises(RuntimeError, match="Production requires"):
        resolve_jwt_secret("production", "local-development-secret-change-this-before-deploy")
    with pytest.raises(RuntimeError, match="Production requires"):
        resolve_jwt_secret("production", "x" * 64)
    strong="A1!b2@C3#d4$E5%f6^G7&h8*I9(j0)K_L-M+N=oP1!q2@R3#s4$T5%u6^V7&W8*"
    assert resolve_jwt_secret("production", strong) == strong
    with pytest.warns(RuntimeWarning,match="development-only JWT secret"):
        assert resolve_jwt_secret("development", None)


def test_token_requires_signature_expiration_issued_at_and_subject():
    subject = "user-123"
    assert decode_token(token_for(subject)) == subject
    incomplete = jwt.encode({"sub":subject,"exp":datetime.now(timezone.utc)+timedelta(minutes=1)}, JWT_SECRET, algorithm="HS256")
    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_token(incomplete)
    expired = jwt.encode({"sub":subject,"iat":datetime.now(timezone.utc)-timedelta(hours=2),"exp":datetime.now(timezone.utc)-timedelta(hours=1)}, JWT_SECRET, algorithm="HS256")
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(expired)


def test_local_rate_limiter_enforces_and_expires_window():
    limiter=LocalRateLimiter(); limiter.policies["login"]=(2,10)
    assert limiter.consume("login","127.0.0.1",now=100)==(True,0)
    assert limiter.consume("login","127.0.0.1",now=101)==(True,0)
    allowed,retry=limiter.consume("login","127.0.0.1",now=102)
    assert not allowed and retry > 0
    assert limiter.consume("login","127.0.0.1",now=111)==(True,0)

def test_rate_limiter_caps_identities_and_total_events():
    limiter=LocalRateLimiter(); limiter.policies["login"]=(10,60); limiter.MAX_IDENTITIES=1
    assert limiter.consume("login","first",now=100)[0]
    assert not limiter.consume("login","second",now=100)[0]
    assert len(limiter.events)==1 and limiter.event_count==1
    limiter.MAX_EVENTS=1
    assert not limiter.consume("login","first",now=101)[0]


class BlockingConnection:
    def __init__(self):
        import threading
        self.interrupted=threading.Event()
    def execute(self, _sql, _parameters=()):
        self.interrupted.wait(1)
        if self.interrupted.is_set(): raise RuntimeError("interrupted")
        return "finished"
    def interrupt(self): self.interrupted.set()


def test_query_timeout_interrupts_and_reports():
    import app.analysis as module
    monkeypatch_timeout=module.QUERY_TIMEOUT_SECONDS
    module.QUERY_TIMEOUT_SECONDS=0.05
    try:
        with pytest.raises(AnalysisTimeout):
            _execute_with_timeout(BlockingConnection(),"SELECT 1")
    finally:
        module.QUERY_TIMEOUT_SECONDS=monkeypatch_timeout


def test_query_timeout_interrupts_real_duckdb_work(monkeypatch):
    import duckdb
    import app.analysis as module
    monkeypatch.setattr(module,"QUERY_TIMEOUT_SECONDS",0.05)
    connection=duckdb.connect(":memory:")
    try:
        with pytest.raises(AnalysisTimeout):
            _execute_with_timeout(connection,"SELECT SUM(hash(i)) FROM range(100000000000) t(i)")
    finally:
        connection.close()


def test_analysis_rejects_over_concurrency_bound(sales_frame, monkeypatch):
    assert _analysis_slots.acquire(blocking=False)
    assert _analysis_slots.acquire(blocking=False)
    try:
        with pytest.raises(AnalysisBusy):
            analyze(sales_frame,"Which product generated the highest revenue?")
    finally:
        _analysis_slots.release(); _analysis_slots.release()


def test_spreadsheet_escaping_preserves_numbers_and_escapes_formula_text():
    source=pd.DataFrame({"text":["=1+1","  @SUM(A1:A2)","normal","-12"],"number":[-12,3.5,None,0]})
    safe=safe_spreadsheet_frame(source)
    assert safe["text"].tolist()==["'=1+1","'  @SUM(A1:A2)","normal","'-12"]
    assert safe["number"].iloc[0]==-12 and safe["number"].iloc[1]==3.5
    assert pd.isna(safe["number"].iloc[2])
