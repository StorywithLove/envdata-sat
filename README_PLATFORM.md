# Satellite Radiation Time-Series Service

A standalone GitHub-based satellite radiation data collector and lightweight visualization project. It does not depend on OCI, a hosted database, Docker, or a manually maintained external database.

## Core data products

1. **Fixed China stations** — 31 mainland provincial-level administrative-center cities, collected by one hourly workflow from the Himawari-9 native 10-minute product. Each run upserts the current UTC day's CSV and quality report:
   `data/daily/YYYY/MM/radiation_YYYYMMDD.csv`.
2. **Custom global coordinates** — provide a CSV of latitude/longitude and a date range; the extractor preserves the selected source's native cadence (for example 10, 15, or 30 minutes) rather than upsampling it.
3. **Latest snapshot and dashboard** — the same hourly collection workflow refreshes the rolling 24-hour `data/cities/latest.json` view for the Web GIS dashboard.

Both extraction modes use the same Open-Meteo satellite radiation API and validate returned timestamps. The fixed Himawari mode requires 600-second cadence.

## Daily time semantics

Open-Meteo's Himawari `shortwave_radiation` values are backward-averaged intervals. A UTC calendar-day CSV for date D therefore contains 144 interval-end labels from D 00:10 through D+1 00:00 UTC. For example, the D+1 00:00 label represents the interval D 23:50–D+1 00:00. The extractor requests the following date as an API boundary, then selects only those 144 interval ends belonging to D.

Missing values remain missing. The extractor does not interpolate, fill with zero, or disguise missing observations as valid nighttime zeros. For fixed-city daily files, `provider_returned_null` means the API returned a timestamp with a null value; `provider_timestamp_missing` means the API omitted an expected 10-minute timestamp. Neither flag proves a satellite outage or a quality-mask cause. Request failures are recorded separately as location failures in the run summary. The sidecar records valid counts, both missing categories, source model, and cadence.

## Run locally

```bash
python -m pip install requests
python src/radiation_extract.py cities --date 2026-11-19
```

Custom coordinates:

```bash
python src/radiation_extract.py custom \
  --coordinates-csv config/custom_locations.example.csv \
  --start-date 2026-11-01 --end-date 2026-11-20 \
  --output output/custom_radiation.csv
```

## GitHub Actions and storage

- The daily workflow runs at 01:30 UTC for the previous UTC day.
- Daily CSV and quality-report files are committed to this repository; a 30-day Actions Artifact is retained as a convenience copy.
- The intended working-tree retention is one year. Automated deletion is deliberately not enabled until the monthly transfer to `StorywithLove/envdata-archive` is implemented and verified; archive confirmation must precede deletion.
- Git commits retain historical blobs even after files are deleted from the working tree. A one-year working-tree policy alone does not prune Git history. No history rewrite is performed automatically.

## Optional local raster experiment

`src/build_hourly_grid.py` is an optional, manually invoked local experiment for producing an approximate hourly raster from JAXA L2 scans. It is not part of the daily point-data archive and is not equivalent to the Open-Meteo backward-averaged product until scan-time correction is implemented and validated.

## Dashboard

The frontend is in `web/` and is deployed with the GitHub Pages workflow. Configure **Settings → Pages → Build and deployment → Source → GitHub Actions**.
