from __future__ import annotations
import re
import pandas as pd

_FORMULA_PREFIX = re.compile(r"^[\t\r\n ]*[=+@\-]")


def safe_spreadsheet_text(value):
    """Prefix formula-like text only; numeric cells (including negatives) remain numeric."""
    if isinstance(value, str) and _FORMULA_PREFIX.match(value):
        return "'" + value
    return value


def safe_spreadsheet_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in result.select_dtypes(include=["object", "string"]).columns:
        result[column] = result[column].map(safe_spreadsheet_text)
    return result
