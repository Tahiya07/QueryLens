import os
import tempfile
import pytest

_folder=tempfile.TemporaryDirectory()
os.environ["QUERYLENS_DATA_DIR"]=_folder.name
os.environ["QUERYLENS_SECRET_KEY"]="pytest-only-secret-value-000000000000000"

@pytest.fixture(scope="session", autouse=True)
def dispose_database_engine():
    yield
    from app.security import _engine
    _engine.dispose()
    _folder.cleanup()

@pytest.fixture(autouse=True)
def reset_rate_limiter():
    from app.main import app
    from app.rate_limit import LocalRateLimiter
    app.state.rate_limiter = LocalRateLimiter()
    yield

@pytest.fixture
def sales_frame():
    import pandas as pd
    return pd.DataFrame({"product":["A","A","B","B","C"],"revenue":[10,15,9,11,40],"region":["East","West","East","East","West"],"units":[1,2,1,1,4]})
