# QueryLens technical notes

## Request lifecycle

1. Authentication validates a bearer JWT and extracts its subject.
2. Dataset routes select metadata by both dataset ID and user ID. Stored paths use generated UUID names under the configured data directory.
3. Upload validation checks extension, byte length, parseability, and row/column bounds. The parser normalizes duplicate headers and detects date-like columns.
4. Profiling and intent recognition run on the uploaded dataframe. The deterministic parser emits a typed `OperationPlan` for aggregate, filter, preview/sort, distribution, correlation, or time-series work; unsupported or ambiguous requests return a clarification.
5. The executor validates referenced columns against the actual dataframe, registers only that dataframe in in-memory DuckDB, disables external access, sets a 512 MB memory cap and two worker threads, and returns bounded results.
6. Successful analyses are saved as history. Export endpoints recompute the operation from the question and produce CSV, XLSX, or PDF.

## Operation vocabulary

| Kind | Input fields | Execution |
|---|---|---|
| `aggregate` | aggregation, numeric measure (optional for count), group column, filters, sort direction, limit | DuckDB `SUM`, `AVG`, `MIN`, `MAX`, or `COUNT`; grouped output capped at 500 rows |
| `filter` | typed conditions, AND/OR, optional sort and limit | Equality/inequality; numeric/date order and between; text contains/starts-with; missing/not-missing |
| `preview` | optional known-column sort and bounded limit | First matching rows, at most 500 |
| `distribution` | numeric column | Non-null sample bounded to 50,000 values, histogram bins and IQR outlier count |
| `correlation` | two to eight numeric columns, Pearson or Spearman | Correlation over at most 50,000 rows; reports complete-case sample size and does not estimate p-values |
| `timeseries` | date, numeric measure, day/month period, optional filters | Period totals; missing calendar periods remain null |

Generated SQL contains fixed syntax, escaped identifiers selected from known dataframe columns, and bound parameters for every filter value. User input is never inserted as a SQL expression. No free-form SQL endpoint exists. Typed planning, validation, execution, result formatting, and explanations are separate stages. DuckDB statements are interrupted after `QUERYLENS_QUERY_TIMEOUT_SECONDS` (default 5 seconds), in-process concurrency is bounded by `QUERYLENS_MAX_CONCURRENT_ANALYSES` (default 2), memory defaults to 512 MB and output rows are capped at 500. Analysis timeouts return 504 and saturated concurrency returns 503. Rate limits are in-process and per client IP; their identity and event storage are capped, configured through `QUERYLENS_RATE_*`, and require no Redis. With multiple API worker processes, rate counters and concurrency bounds are per process.

Production startup rejects missing, placeholder, short, or low-diversity JWT signing secrets. Development keeps a convenience fallback and emits a warning. Tokens require `sub`, `iat`, and `exp` and are signed with HS256. The browser stores its bearer token in `sessionStorage`, which limits persistence to the tab session but does not make the token inaccessible to same-origin JavaScript or XSS. This is a local-first security improvement, not a complete production session design. Passwords are limited to 72 UTF-8 bytes to match bcrypt's input limit.

CSV and XLSX exports prefix formula-like text values with an apostrophe. Numeric values, including negative numeric cells, retain their numeric type.

Time-series aggregation sums non-null measure values. Missing calendar periods are returned with null values and a `missing_period` status; they are never treated as zero. Period growth is calculated only against the immediately preceding calendar period with a non-null, non-zero total. Missing, null, zero-baseline, and first-period cases have explicit `growth_status` values. Negative baselines use the standard percentage-change formula, but may not have an intuitive conventional-growth interpretation.

## History and reproducibility

Successful analysis requests store the question, execution timestamp, typed operation, query text and bound parameters, result rows, explanation, and provenance in the existing `analyses.result_json`. Provenance includes dataset ID and SHA-256 fingerprint of the stored file, selected result columns, result row count, and known limitations. History listing returns successful questions and timestamps; replay reruns the question against the selected dataset. The schema is unchanged, so no migration is required. Stored results can contain sensitive analysis output; protect the metadata database and data directory.

## Supported question examples

- `Show sales where region equals East and revenue greater than 500`
- `Show rows where customer name contains Smith`
- `Show rows where revenue is missing`
- `Show transactions where date between 2025-01-01 and 2025-03-31`
- `Show the top 10 products by revenue`
- `Find the five regions with the lowest average sales`
- `Sort customers by purchase amount ascending`
- `Compare average revenue by region`
- `Correlation between revenue and units`

Mixed AND/OR expressions requiring parentheses, unrecognized columns, and incompatible value types request clarification. Pearson and Spearman coefficients are supported; statistical significance tests, confidence intervals, and causal inference are not implemented.

## Storage and schema

SQLite schema starts at [0001_initial.sql](../backend/migrations/0001_initial.sql); PostgreSQL uses [0001_initial_postgresql.sql](../backend/migrations/0001_initial_postgresql.sql). Metadata uses PostgreSQL when `QUERYLENS_DATABASE_URL` is configured and SQLite otherwise. Dataset values remain as server-created pickle files under `QUERYLENS_DATA_DIR/datasets/`; only validated CSV or Excel files are converted into these files. Do not allow untrusted processes to write directly to this data directory.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `QUERYLENS_DATA_DIR` | `./data` | SQLite database and dataset storage root |
| `QUERYLENS_DATABASE_URL` | unset (SQLite) | SQLAlchemy URL for PostgreSQL, e.g. `postgresql+psycopg://user:password@localhost:5432/querylens` |
| `QUERYLENS_SECRET_KEY` | local-only development value | HS256 signing key; replace outside development |
| `QUERYLENS_ENV` | `development` | Set to `production` to enforce a strong explicit signing key |
| `QUERYLENS_QUERY_TIMEOUT_SECONDS` | `5` | Per-DuckDB-statement timeout, 0.05–120 seconds |
| `QUERYLENS_MAX_CONCURRENT_ANALYSES` | `2` | In-process analysis slots, 1–32 |
| `QUERYLENS_DUCKDB_MEMORY_LIMIT` | `512MB` | DuckDB memory cap, at most 2048 MB |
| `QUERYLENS_RATE_*` | action-specific | Per-process limits/windows for registration, login, analysis, and export |
| `QUERYLENS_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed browser origins |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Frontend API origin, embedded at build time |

## API routes

| Method and route | Purpose |
|---|---|
| `POST /api/auth/register`, `POST /api/auth/login` | Account creation and bearer-token issuance |
| `GET /api/datasets` | Caller-owned dataset list |
| `POST /api/datasets` | CSV/XLSX/XLS upload and validation |
| `GET /api/datasets/{id}` | Metadata, profile, and preview |
| `DELETE /api/datasets/{id}` | Delete owned dataset and dependent history |
| `POST /api/datasets/{id}/analyze` | Execute and persist analysis/provenance |
| `GET /api/datasets/{id}/history` | Recent successful questions and timestamps |
| `POST /api/datasets/{id}/export` | CSV, XLSX, or PDF report |
| `GET /api/health` | Health and LLM-disabled status |
