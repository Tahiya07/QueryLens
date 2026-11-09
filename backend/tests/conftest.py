import os
import tempfile
import pytest

_folder=tempfile.TemporaryDirectory()
os.environ["QUERYLENS_DATA_DIR"]=_folder.name
os.environ["QUERYLENS_SECRET_KEY"]="pytest-only-secret-value"

@pytest.fixture(scope="session", autouse=True)
def dispose_database_engine():
    yield
    from app.security import _engine
    _engine.dispose()
    _folder.cleanup()

@pytest.fixture
def sales_frame():
    import pandas as pd
    return pd.DataFrame({"product":["A","A","B","B","C"],"revenue":[10,15,9,11,40],"region":["East","West","East","East","West"],"units":[1,2,1,1,4]})
