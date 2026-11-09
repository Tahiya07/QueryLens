# QueryLens technical notes

## Request lifecycle

1. Authentication validates a bearer JWT and extracts its subject.
2. Dataset routes select metadata by both dataset ID and user ID. Stored paths use generated UUID names under the configured data directory.
3. Upload validation checks extension, byte length, parseability, and row/column bounds. The parser normalizes duplicate headers and detects date-like columns.
4. Profiling and intent recognition run on the uploaded dataframe. The parser emits `aggregate`, `distribution`, `correlation`, or `preview`; unsupported requests return a clarification.
5. The executor validates referenced columns against the actual dataframe, registers only that dataframe in in-memory DuckDB, disables external access, sets a 512 MB memory cap and two worker threads, and returns bounded results.
6. Successful analyses are saved as history. Export endpoints recompute the operation from the question and produce CSV, XLSX, or PDF.

## Operation vocabulary

| Kind | Input fields | Execution |
|---|---|---|
| `aggregate` | aggregation, numeric measure (optional for count), group column (optional for count) | DuckDB `SUM`, `AVG`, `MIN`, `MAX`, or `COUNT`; grouped output sorted descending and capped at 500 rows |
| `distribution` | numeric column | Non-null sample bounded to 50,000 values, histogram bins and IQR outlier count |
| `correlation` | two to eight numeric columns | Pearson correlation over at most 50,000 rows |
| `preview` | fixed row limit | First 100 records |

Generated SQL contains fixed syntax and escaped identifiers selected from known dataframe columns. User input is not inserted into SQL as a predicate or expression. No free-form SQL endpoint exists. The implementation does not yet provide arbitrary filters, significance tests, or statement timeout.

## Storage and schema

SQLite schema starts at [0001_initial.sql](../backend/migrations/0001_initial.sql); PostgreSQL uses [0001_initial_postgresql.sql](../backend/migrations/0001_initial_postgresql.sql). Metadata uses PostgreSQL when `QUERYLENS_DATABASE_URL` is configured and SQLite otherwise. Dataset values remain as server-created pickle files under `QUERYLENS_DATA_DIR/datasets/`; only validated CSV or Excel files are converted into these files. Do not allow untrusted processes to write directly to this data directory.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `QUERYLENS_DATA_DIR` | `./data` | SQLite database and dataset storage root |
| `QUERYLENS_DATABASE_URL` | unset (SQLite) | SQLAlchemy URL for PostgreSQL, e.g. `postgresql+psycopg://user:password@localhost:5432/querylens` |
| `QUERYLENS_SECRET_KEY` | local-only development value | HS256 signing key; replace outside development |
| `QUERYLENS_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed browser origins |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Frontend API origin, embedded at build time |
