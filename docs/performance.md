# Performance measurements

Measured on the Windows development host with Python 3.12.6 and pinned dependencies, using `backend/benchmark.py`. The benchmark builds an in-memory dataframe of 100,000 rows and 3 columns, runs one profile and one grouped total analysis, then reports the process peak working set.

Latest recorded run:

```json
{
  "python": "3.12.6",
  "platform": "Windows-11-10.0.26300-SP0",
  "rows": 100000,
  "profile_seconds": 0.0564,
  "analysis_seconds": 0.0265,
  "peak_rss_mb": 91.57
}
```

These are single-run local measurements, not a capacity guarantee. Rerun the script on the target machine. They exclude browser memory, upload/network time, concurrency, and PostgreSQL.
