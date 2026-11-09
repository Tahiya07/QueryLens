# Milestone 2 implementation checklist

## Audit baseline (before changes)

- [x] Read `README.md`, `docs/technical.md`, and `docs/performance.md`.
- [x] Inspect all backend app modules and current backend tests.
- [x] Inspect the frontend workspace, CSS, E2E test, and package configuration.
- [x] Backend baseline: `py -3.12 -m pytest -q` — 18 passed.
- [x] Frontend baseline: `npm run build` — passed.
- [x] Browser baseline: `npm run e2e` with local API — 1 passed.
- [x] Record discrepancies: JWT has a known fallback; no request rate limiting or query timeout; formula escaping absent; time series skips missing periods; workspace sidebar has nonfunctional destinations; operation output is untyped and provenance is sparse.

## Priority order

### P0 — correctness, security, reliability

- [x] Validate production JWT secret and required JWT claims; preserve a development-only default.
- [x] Move browser token persistence from `localStorage` to tab-scoped `sessionStorage` and document the remaining XSS trade-off.
- [x] Add bounded in-process rate limiting for registration, login, analysis, and export.
- [x] Add DuckDB statement timeout, concurrency bound, bounded memory/results, timeout cleanup, and sanitized timeout response.
- [x] Harden CSV/XLSX ingestion for encodings, empty/malformed headers, partial invalid dates, and boundary limits.
- [x] Escape formula-like strings in CSV and Excel exports while preserving numeric values.
- [x] Correct time-series calendar handling: retain absent periods as explicit nulls, calculate growth only across adjacent observed periods, and explain zero/null/negative baselines.
- [x] Add deterministic P0 regression and boundary tests.

### P1 — analytical operations

- [x] Introduce typed operation models and separate parse, validation, execution, formatting, and explanation.
- [x] Add safe filters with AND/OR, null, numeric/date/string operators, and parameterized values.
- [x] Add explicit sort direction and bounded top/bottom N with correct grouped ranking.
- [x] Add segmented comparisons and grouped summaries.
- [x] Add tests for parser ambiguity, type validation, SQL safety, filters, and ranking.

### P2 — workspace navigation and usability

- [x] Make Overview, Datasets, Analysis, Query history, Settings, and Documentation destinations functional.
- [x] Use the existing history endpoint to select/replay prior queries.
- [x] Add empty/loading/error states, accessible controls, and deletion confirmation.
- [x] Add frontend coverage for navigation and history replay; filter/sort behavior is covered at the API and backend operation layer.

### P3 — visualization and P4 statistics

- [x] Add vertical/horizontal bar chart choices only for bar-compatible results; cap plotted series and preserve null time-series gaps.
- [x] Add Spearman correlation as a descriptive method with complete-case sample size and explicit no-p-value limitation.
- [ ] Add inferential statistical tests with assumptions, null hypotheses, p-values, and limitations.

### P5 — provenance and final verification

- [x] Persist dataset fingerprint, normalized operation, execution metadata, result summary, and limitations in existing history JSON.
- [x] Update technical/API/security/performance documentation.
- [x] Run backend tests, frontend build/type checks, and browser E2E after implementation.

Do not mark items complete unless the behavior and corresponding verification exist. PostgreSQL remains optional and SQLite remains the local default.
