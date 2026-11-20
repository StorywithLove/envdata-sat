# envdata-sat — Himawari Satellite Radiation Data

## Project role

`envdata-sat` is an independent, lightweight data-acquisition and validation project. Its purpose is to retrieve near-real-time satellite shortwave radiation (GHI/SWR), preserve daily station CSVs, validate the upstream data, and provide a small web visualization.

It is **not** an OCI-hosted service and does not depend on OCI, PostgreSQL, a user-managed database, or manual station-data uploads.

Related repositories have separate responsibilities:

- **envdata-sat**: acquire and validate daily satellite radiation data; retain the recent daily CSV collection and provide quick visualization.
- **env-archive**: receive historical monthly batches from envdata-sat. Its monthly archival workflow is responsible for long-term history.
- **envdata-sync**: reference for the automated synchronization pattern and lightweight repository-based data handling.

No cross-repository credentials or archive automation are assumed to exist until configured and tested in env-archive.

## Core functions

### 1. Fixed national stations

The built-in station list contains 31 mainland provincial-level administrative centers in `config/cities.json`.

Every day, the scheduled workflow requests the previous complete UTC calendar day from the Open-Meteo Satellite Radiation API and writes:

```text
data/daily/YYYY/MM/radiation_YYYYMMDD.csv
data/daily/YYYY/MM/radiation_YYYYMMDD.status.json
```

For Himawari-9, the expected native cadence is 10 minutes, or 144 timestamp slots per station per complete UTC day. This is an expected timeline, **not a requirement for 144 valid observations**. Missing values are retained as missing and summarized in the status report.

The workflow commits the generated daily CSV and status JSON to this repository. GitHub Actions artifacts are an additional short-lived run output, not the canonical daily store.

### 2. Custom coordinate extraction

The same extractor supports one or multiple arbitrary coordinates and a caller-specified date range:

```bash
pip install requests
python src/radiation_extract.py custom \
  --coordinates-csv config/custom_locations.example.csv \
  --start-date 2026-11-01 --end-date 2026-11-20 \
  --output output/custom_radiation.csv
```

The global mode uses Open-Meteo satellite source selection and preserves the provider's native cadence (for example 10, 15, or 30 minutes). It never upsamples lower-frequency data to 10 minutes. Each output has a JSON quality report.

### 3. Near-real-time snapshot and visualization

The realtime workflow refreshes the rolling city snapshot used by the static Leaflet/Chart.js dashboard. GitHub Pages is the lightweight presentation layer; it is not the historical data store.

The realtime collector runs every 15 minutes (GitHub schedule is best-effort), can be manually dispatched, and runs after relevant source/configuration changes on `main`.

## Data source and semantics

- Primary data access: Open-Meteo Satellite Radiation API.
- Fixed China stations: `models=jma_jaxa_himawari`, `temporal_resolution=native`.
- GHI variable: `shortwave_radiation`, unit W/m².
- Himawari-9 native cadence over the fixed China stations: 10 minutes.
- The API has product latency; the latest slots may not yet have been published.

Himawari observations may contain nighttime gaps, scheduled maintenance/outage intervals, unavailable scans, or upstream publication gaps. These are expected data characteristics, not automatically software failures. The pipeline should preserve null/missing slots, report their distribution, and distinguish expected upstream gaps from request/parse/cadence errors. It must not fabricate observations by interpolation or replace missing values with zero.

JAXA P-Tree L2 SWR is a raw instantaneous scan sample. The optional legacy fallback is not physically time-equivalent to Open-Meteo's scan-time-corrected backward averages. Open-Meteo and JAXA P-Tree are also two access/processing paths to the same Himawari data family, not independent satellite observations. Do not silently merge the raw fallback into the validated GHI series.

## Daily retention and historical archive

- envdata-sat generates and commits one daily CSV plus a sidecar quality report.
- The intended recent-data retention policy follows the lightweight envdata-sync pattern.
- env-archive is the long-term historical destination and is intended to run a monthly job at the beginning of each month to archive the previous month's daily CSVs.
- The monthly cross-repository transfer, verification, and any source-side retention cleanup must be implemented and tested in env-archive before old daily files are deleted from envdata-sat. Until then, do not automatically prune source data.

This separation keeps envdata-sat small and focused while allowing env-archive to own long-term history.

## Run locally

Fixed station daily CSV:

```bash
pip install requests
python src/radiation_extract.py cities --date 2026-11-19
```

Custom coordinates:

```bash
python src/radiation_extract.py custom \
  --coordinates-csv config/custom_locations.example.csv \
  --start-date 2026-11-01 --end-date 2026-11-20 \
  --output output/custom_radiation.csv
```

Realtime snapshot:

```bash
pip install requests numpy netCDF4
python src/radiation_pipeline.py
```

## Lightweight dashboard

The frontend is in `web/` and is deployed with GitHub Pages. Set repository **Settings → Pages → Build and deployment → Source** to **GitHub Actions**.

The current dashboard presents fixed city locations, latest available values, and recent time series. It is intended for quick inspection and data-source validation, not as a full operational GIS platform.

## Optional / experimental raster processing

`src/build_hourly_grid.py` is an experimental local/manual raster builder for a China window. It creates an approximate hourly mean of instantaneous JAXA L2 scans. It is not scan-time corrected and is not directly equivalent to Open-Meteo's backward-averaged GHI. Treat it as a separate experiment, not part of the daily CSV core workflow.

## Explicit non-goals

- No PostgreSQL or other database dependency.
- No OCI deployment, OCI persistent disk, object storage, or server provisioning.
- No requirement for manual station data uploads.
- No claim that all global locations have 10-minute satellite observations.
- No interpolation to disguise expected nighttime, maintenance, or upstream data gaps.
- No automatic deletion of daily source CSVs before the env-archive monthly transfer is verified.
