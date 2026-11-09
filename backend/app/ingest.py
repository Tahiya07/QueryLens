from __future__ import annotations
import io
import csv
from pathlib import Path
import pandas as pd

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_ROWS = 500_000
MAX_COLUMNS = 300
ALLOWED = {".csv", ".xlsx", ".xls"}


class UploadValidationError(ValueError):
    pass


def read_dataset(filename: str, data: bytes) -> pd.DataFrame:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED:
        raise ValueError("Upload a CSV, XLSX, or XLS file.")
    if not data or not data.strip() or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("File is empty or exceeds the 25 MB upload limit.")
    try:
        if suffix == ".csv":
            text = None
            for candidate in ("utf-8-sig", "cp1252", "latin-1"):
                try:
                    text = data.decode(candidate)
                    break
                except UnicodeDecodeError:
                    continue
            if text is None:
                raise UploadValidationError("CSV text encoding is not supported.")
            reader = csv.reader(io.StringIO(text), strict=True)
            header = next(reader)
            if not header or not any(str(value).strip() for value in header):
                raise UploadValidationError("The CSV does not contain a usable header row.")
            if len(header) > MAX_COLUMNS:
                raise UploadValidationError("Dataset exceeds the supported limit of 300 columns.")
            row_count = 0
            for row_count, row in enumerate(reader, start=1):
                if len(row) > len(header):
                    raise UploadValidationError("CSV contains a row with more fields than its header.")
                if row_count > MAX_ROWS:
                    raise UploadValidationError("Dataset exceeds the supported limit of 500,000 rows.")
            unique_header, seen = [], set()
            for index, raw in enumerate(header):
                label = str(raw).strip() or f"column_{index + 1}"
                base, candidate, number = label, label, 2
                while candidate in seen:
                    candidate, number = f"{base}_{number}", number + 1
                seen.add(candidate)
                unique_header.append(candidate)
            header = unique_header
            frame = pd.read_csv(io.StringIO(text), low_memory=False, names=header, header=0, nrows=MAX_ROWS + 1)
        else:
            frame = pd.read_excel(io.BytesIO(data), engine="openpyxl" if suffix == ".xlsx" else "xlrd", nrows=MAX_ROWS + 1)
    except UploadValidationError:
        raise
    except Exception as exc:
        raise ValueError("The file could not be parsed. Check that it is a valid, unencrypted spreadsheet.") from exc
    if frame.empty and len(frame.columns) == 0:
        raise ValueError("The file does not contain a header row or data.")
    if len(frame) > MAX_ROWS or len(frame.columns) > MAX_COLUMNS:
        raise ValueError("Dataset exceeds the supported limit of 500,000 rows or 300 columns.")
    if not len(frame.columns):
        raise ValueError("The dataset has no columns.")
    for column in frame.select_dtypes(include="object").columns:
        series = frame[column]
        non_missing = series.dropna()
        if non_missing.empty:
            continue
        date_named = any(token in str(column).lower() for token in ("date", "time", "timestamp"))
        looks_like_date = non_missing.astype(str).str.contains(r"[-/:]").mean() >= 0.8
        if date_named or looks_like_date:
            parsed = pd.to_datetime(series, errors="coerce", format="mixed", utc=False)
            minimum_valid = max(1, int(len(non_missing) * (0.5 if date_named else 0.8)))
            if parsed.notna().sum() >= minimum_valid:
                frame[column] = parsed
    # Stable, unique labels prevent ambiguity while preserving recognizable names.
    names, seen = [], set()
    for index, raw in enumerate(frame.columns):
        label = str(raw).strip() or f"column_{index + 1}"
        base, candidate, number = label, label, 2
        while candidate in seen:
            candidate, number = f"{base}_{number}", number + 1
        seen.add(candidate)
        names.append(candidate)
    frame.columns = names
    return frame
