# QueryLens

QueryLens is a local-first data analysis workspace. Upload CSV or Excel files, inspect their profile, and ask a conservative deterministic parser to produce restricted aggregations, distributions, previews, or correlations. It works without an LLM and never executes model-generated Python or user-supplied SQL.

## Status checklist

- [x] CSV/XLSX/XLS ingestion, size and dimension limits, header normalization
- [x] Per-account dataset ownership, account registration/login, password hashing, signed expiring bearer tokens
- [x] Profiling: types, missingness, unique values, numeric summaries, IQR outlier indicators, duplicates, correlations
- [x] Deterministic intent parser; asks for clarification on unsupported or ambiguous requests
- [x] Restricted DuckDB query path with escaped allowlisted identifiers, bounded results, memory/thread limits
- [x] Analysis history and CSV, XLSX, PDF exports
- [x] Responsive dark workspace with dataset preview, profile table, analysis results and charts
- [x] Docker files, sample data, API docs, architecture and benchmark runner
- [x] Backend tests for ingestion, profiling, intent, safety, accuracy, auth, isolation and exports
- [x] Versioned SQLite and PostgreSQL schema migrations
- [x] Browser end-to-end journey and screenshot of an analyzed sample
- [ ] Production session hardening, rate limiting, and operational monitoring

The checked items are implemented; unchecked items are known work, not completed features. Do not expose this deployment directly to the public internet without addressing the listed production gaps.

## Architecture

```mermaid
flowchart LR
  Browser[Next.js workspace] -->|Bearer token + REST| API[FastAPI]
  API --> Auth[SQLite or PostgreSQL metadata]
  API --> Store[Private per-user dataset files]
  API --> Ingest[CSV and Excel validation]
  Ingest --> Profile[Data profiling]
  API --> Intent[Deterministic intent parser]
  Intent --> Plan[Allowlisted analytical operation]
  Plan --> Duck[In-memory DuckDB]
  Duck --> Result[Rows, explanation, chart spec]
  Result --> Browser
  Result --> Export[CSV, XLSX or PDF]
```

Uploaded names are reduced to basenames for display; generated UUIDs determine on-disk names. Dataset lookup always includes the authenticated owner ID. Analysis SQL is constructed only from parser-selected columns that exist in the uploaded frame, with identifiers escaped and fixed result limits. User text is never treated as SQL.

## Run on Windows

Python 3.12 and Node.js 20+ are recommended. Open two PowerShell terminals from the repository root.

### Backend

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
cd backend
python -m pip install -e ".[dev]"
$env:QUERYLENS_SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(48))"
$env:QUERYLENS_DATA_DIR = "$PWD\..\data"
uvicorn app.main:app --reload
```

Interactive API docs: <http://localhost:8000/docs>.

### Frontend

In a second PowerShell terminal opened at the repository root:

```powershell
cd frontend
npm ci
$env:NEXT_PUBLIC_API_URL = "http://localhost:8000"
npm run dev
```

Open <http://localhost:3000>, create an account, upload `sample-data/sales.csv`, and try “Which product generated the highest revenue?” or “Show distribution of revenue”.

### Docker Compose

Copy `.env.example` to `.env`, replace the signing key and PostgreSQL password, then run `docker compose up --build`. Compose starts PostgreSQL for metadata and persists it and the dataset files in Docker volumes. For a direct local backend run, SQLite remains the default. The frontend API URL is embedded at build time, so set `NEXT_PUBLIC_API_URL` in the Compose build args when accessing it from another host.

## Tests and benchmark

```powershell
cd backend
python -m pytest -q
python benchmark.py
```

The benchmark generates a deterministic 100,000-row in-memory frame and reports measured profiling/analysis duration and peak working set. See [the recorded measurement](docs/performance.md). For the browser journey, start the backend as above, then run `npm run e2e` from `frontend`; it saves `screenshots/analysis-workspace.png`.

## Supported question shapes

- “Average revenue by region”
- “Which product generated the highest revenue?”
- “Lowest units by product”
- “Show unusual values in revenue” (IQR indicator; not an error diagnosis)
- “Show monthly sales growth” (sums a date-aware measure and calculates period-over-period change)
- “Correlation between revenue and units”
- “Show a preview”

Columns should be named explicitly when multiple numeric measures exist. Supported operations are intentionally limited: arbitrary filters and statistical significance tests are not implemented yet. Time series support monthly or daily period totals and change; more advanced forecasting is not included. Correlation is association, not causation. The parser's supported language is heuristic and responds with a clarification where it cannot identify a supported operation.

## Data and security notes

- Upload maximum: 25 MB; 500,000 rows; 300 columns.
- Metadata uses SQLite by default or PostgreSQL when `QUERYLENS_DATABASE_URL` is configured. Dataset files remain in `QUERYLENS_DATA_DIR` on the API host.
- Authentication uses bcrypt password hashes and 12-hour signed bearer tokens. The browser keeps the token in local storage; this is suitable for a local portfolio build but needs a hardened session design and rate limiting before public production use.
- DuckDB operates on a registered in-memory dataframe. The application does not accept SQL input. Results are capped at 500 rows (50,000 source rows for correlation/distribution calculations), and DuckDB is configured for 512 MB and two threads.
- XLS legacy files require `xlrd`; XLSX uses `openpyxl`. Encrypted files are rejected with a parse error.
- PDF exports include dataset/question/calculation metadata and up to 35 result rows; XLSX exports have Results and Analysis sheets.
- No local model is bundled or downloaded. LLM integration is not implemented and inference is disabled.

## API outline

FastAPI OpenAPI is served at `/openapi.json`, with interactive docs at `/docs`.

| Route | Purpose |
|---|---|
| `POST /api/auth/register`, `/api/auth/login` | Create account / obtain bearer token |
| `GET /api/datasets` | List caller-owned datasets |
| `POST /api/datasets` | Upload CSV/XLSX/XLS |
| `GET /api/datasets/{id}` | Profile and preview |
| `DELETE /api/datasets/{id}` | Delete dataset and analysis history |
| `POST /api/datasets/{id}/analyze` | Parse and execute a question |
| `GET /api/datasets/{id}/history` | Recent understood queries |
| `POST /api/datasets/{id}/export` | Export a question result as csv/xlsx/pdf |

## Development notes

Dependencies are pinned in `backend/pyproject.toml` and `frontend/package.json`. `npm install` creates the lockfile for repeatable `npm ci`. Sample expected calculations are exercised in backend tests. Authentication and uploaded file data are intentionally not committed.

See [technical notes](docs/technical.md) for the request lifecycle, operation schema, storage design, and configuration.
