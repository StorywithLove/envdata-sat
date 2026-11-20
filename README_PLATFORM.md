# China Satellite Radiation Data Platform (MVP)

This branch begins the transition from a single-site Himawari archive script to a multi-city radiation data service.

## Current MVP scope

- 31 mainland provincial-level administrative-center cities.
- Open-Meteo Satellite Radiation API as the primary city-point source.
- Native satellite temporal resolution (Himawari-9: 10 minutes).
- Optional JAXA P-Tree FTP fallback for null/missing Open-Meteo samples in the latest hour.
- A read-only FastAPI service over the latest rolling snapshot.
- No PostgreSQL dependency.

## Data semantics and caveats

Open-Meteo's `shortwave_radiation` is GHI in W/m². The pipeline requests native satellite samples using `hourly_native=true`; the Himawari-9 product has a native 10-minute cadence. The data source has a latency of roughly 30 minutes.

JAXA P-Tree L2 `SWR` is a raw scan sample. The current fallback extracts the nearest grid cell from the corresponding scan file and labels it `fallback_raw_instant_not_time_equivalent`. It is useful for identifying a likely missing observation, but must not be silently treated as the same physical time average as the Open-Meteo record. A later processing stage should implement and validate scan-time and interval averaging correction before these records are used for quantitative model evaluation.

Open-Meteo's Himawari product and JAXA P-Tree FTP both originate from the JMA/JAXA Himawari-9 data family. They are alternate processing/access paths, not independent satellite observations.

## Run collection locally

```bash
pip install requests numpy netCDF4
# Optional: configure H8_USERNAME and H8_PASSWORD for JAXA FTP fallback.
python src/radiation_pipeline.py
```

Output:
- `data/cities/latest.json`: rolling city-point snapshot (about 24 hours).
- `data/cities/status.json`: update time, completeness counts, failures, and provenance notes.

The workflow runs every 15 minutes, can be triggered manually, and runs once when the collection code/configuration or its workflow definition changes on `main`. GitHub Actions schedules are best-effort, not a hard real-time scheduler. The separate Pages workflow deploys the dashboard when `web/` or `data/cities/` changes.

## Run the API locally

```bash
pip install -r api/requirements.txt
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Endpoints:
- `GET /health`
- `GET /api/v1/status`
- `GET /api/v1/cities`
- `GET /api/v1/radiation/point?city=wuhan`
- Optional UTC filters: `start=2026-11-20T00:00:00Z&end=2026-11-20T12:00:00Z`

## Required GitHub secrets

- `H8_USERNAME`
- `H8_PASSWORD`

These are only used for JAXA FTP fallback. Open-Meteo collection does not require credentials. `DB_HOST` and `DB_PWD` are no longer used by the realtime workflow.

## Publish the static dashboard

The frontend is in `web/` and can be deployed with the Pages workflow. After merging this branch, open **Settings → Pages** and set the build/deployment source to **GitHub Actions**. The workflow copies the latest city snapshot into the static site and deploys the map dashboard. The dashboard currently shows city-point values and time series; the full-resolution raster/COG grid layer is a subsequent milestone.

## China gridded raster generation (local/OCI worker)

The first raster-processing component is available as `src/build_hourly_grid.py`. It downloads the six expected 10-minute JAXA L2 SWR scans for one UTC hour, crops the China window (70–140°E, 15–55°N), and writes a compressed GeoTIFF/COG candidate plus a JSON sidecar.

```bash
pip install -r grid-requirements.txt
export H8_USERNAME="..."
export H8_PASSWORD="..."
python src/build_hourly_grid.py --date 20261120 --hour 3
```

By default it processes the latest completed UTC hour (one-hour safety delay). It requires at least four scans and, by default, at least four valid samples per pixel. Output is written under `data/grid/hourly/YYYY/MM/DD/` and is intentionally excluded from Git history. The same builder is exposed through the manual **Build hourly China radiation raster** Actions workflow; its output is uploaded as a short-lived (7-day) artifact for inspection, not used as permanent storage.

**Scientific caveat:** this is an approximate hourly mean of instantaneous L2 scans. It is not scan-time corrected and is not directly equivalent to Open-Meteo's backward-averaged GHI. Metadata records the scan list and this limitation. Do not use it for quantitative cross-source evaluation until the temporal correction is implemented and validated.

The COG output is the raster-processing milestone, not yet a hosted GIS layer. A persistent object-storage bucket and a tile-serving endpoint (for example, TiTiler) are still needed before the frontend can overlay the national grid. The raster workflow is intentionally manual until a durable object-storage destination is configured; it does not push large imagery into Git. Large raster files should not be committed to this Git repository.

## Next milestones

1. Validate city coverage, completeness, and Open-Meteo/JAXA overlap over several days.
2. Correct JAXA scan-time and temporal averaging semantics before quantitative gap filling.
3. Publish the API on a small service host.
4. Add the China raster/COG ingestion path and a MapLibre-based GIS frontend with a time slider.
