#!/usr/bin/env python3
"""Collect city-level satellite GHI from Open-Meteo, with optional JAXA L2 gap checks."""
from __future__ import annotations
import json, logging, os, subprocess, tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import numpy as np
import requests
from netCDF4 import Dataset

ROOT = Path(__file__).resolve().parents[1]
CITY_FILE = ROOT / "config" / "cities.json"
DATA_DIR = ROOT / "data" / "cities"
LATEST_FILE = DATA_DIR / "latest.json"
STATUS_FILE = DATA_DIR / "status.json"
API_URL = "https://satellite-api.open-meteo.com/v1/archive"
LOG = logging.getLogger("radiation-pipeline")

def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)

def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)

def fetch_city(city: dict[str, Any]) -> list[dict[str, Any]]:
    params = {
        "latitude": city["latitude"], "longitude": city["longitude"],
        "hourly": "shortwave_radiation", "models": "satellite_radiation_seamless",
        "hourly_native": "true", "past_hours": 25, "forecast_hours": 1,
        "timezone": "UTC",
    }
    response = requests.get(API_URL, params=params, timeout=45)
    response.raise_for_status()
    hourly = response.json().get("hourly") or {}
    times, values = hourly.get("time") or [], hourly.get("shortwave_radiation") or []
    if len(times) != len(values):
        raise ValueError(f"Mismatched time/value arrays for {city['id']}")
    return [{
        "city_id": city["id"], "city": city["name"], "province": city["province"],
        "latitude": city["latitude"], "longitude": city["longitude"],
        "time_utc": parse_time(t).isoformat().replace("+00:00", "Z"),
        "ghi_wm2": None if v is None else float(v), "unit": "W/m²",
        "source": "open_meteo_himawari9",
        "quality_flag": "valid" if v is not None else "missing",
        "temporal_semantics": "backward_average_native_10min",
    } for t, v in zip(times, values)]

def jaxa_filename(scan_start: datetime) -> str:
    return f"H09_{scan_start:%Y%m%d_%H%M}_RFL021_FLDK.02801_02401.nc"

def download_jaxa(scan_start: datetime, target: Path) -> bool:
    username, password = os.getenv("H8_USERNAME"), os.getenv("H8_PASSWORD")
    if not username or not password:
        return False
    filename = jaxa_filename(scan_start)
    remote = ("ftp://ftp.ptree.jaxa.jp/pub/himawari/L2/PAR/021/"
              f"{scan_start:%Y%m}/{scan_start:%d}/{scan_start:%H}/{filename}")
    command = ["wget", "-q", "--timeout=30", "--tries=2",
               f"--ftp-user={username}", f"--ftp-password={password}",
               "-O", str(target), remote]
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    return result.returncode == 0 and target.exists() and target.stat().st_size > 0

def extract_jaxa_pixels(nc_path: Path, cities: list[dict[str, Any]]) -> dict[str, float]:
    extracted = {}
    with Dataset(nc_path) as dataset:
        latitudes = np.asarray(dataset.variables["latitude"][:])
        longitudes = np.asarray(dataset.variables["longitude"][:])
        swr = dataset.variables["SWR"][:]
        for city in cities:
            lon = city["longitude"]
            if np.nanmax(longitudes) > 180 and lon < 0:
                lon %= 360
            row = int(np.nanargmin(np.abs(latitudes - city["latitude"])))
            col = int(np.nanargmin(np.abs(longitudes - lon)))
            value = swr[row, col]
            if np.ma.is_masked(value):
                continue
            number = float(value)
            if np.isfinite(number) and number >= 0:
                extracted[city["id"]] = number
    return extracted

def fill_gaps_with_jaxa(rows: list[dict[str, Any]], cities: list[dict[str, Any]]) -> int:
    # Limit automatic FTP repair to the most recent hour; older gaps remain visible
    # in status/data and can be handled by a dedicated backfill workflow.
    recent_cutoff = utc_now() - timedelta(hours=1)
    missing_times = sorted({parse_time(r["time_utc"]) for r in rows
                            if r["ghi_wm2"] is None and parse_time(r["time_utc"]) >= recent_cutoff})
    if not missing_times:
        return 0
    rows_by_key = {(r["city_id"], r["time_utc"]): r for r in rows}
    filled = 0
    with tempfile.TemporaryDirectory(prefix="jaxa-gap-") as tmp:
        for interval_end in missing_times:
            # JAXA labels the scan start; Open-Meteo labels the backward interval end.
            scan_start = interval_end - timedelta(minutes=10)
            nc_path = Path(tmp) / jaxa_filename(scan_start)
            if not download_jaxa(scan_start, nc_path):
                LOG.warning("JAXA file unavailable for nominal scan %s", scan_start.isoformat())
                continue
            try:
                pixels = extract_jaxa_pixels(nc_path, cities)
            except Exception:
                LOG.exception("Could not read JAXA file %s", nc_path.name)
                continue
            finally:
                nc_path.unlink(missing_ok=True)
            time_key = interval_end.isoformat().replace("+00:00", "Z")
            for city_id, value in pixels.items():
                row = rows_by_key.get((city_id, time_key))
                if row is not None and row["ghi_wm2"] is None:
                    row.update({"ghi_wm2": value, "source": "jaxa_himawari9_l2",
                                "quality_flag": "fallback_raw_instant_not_time_equivalent",
                                "temporal_semantics": "instantaneous_scan_unadjusted"})
                    filled += 1
    return filled

def complete_timeline(rows: list[dict[str, Any]], cities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Materialize missing 10-minute slots so delayed/absent API samples can be checked."""
    now = utc_now()
    latest = now.replace(minute=(now.minute // 10) * 10)
    first = latest - timedelta(hours=24)
    indexed = {(row["city_id"], row["time_utc"]): row for row in rows}
    result = []
    for city in cities:
        timestamp = first
        while timestamp <= latest:
            key = (city["id"], timestamp.isoformat().replace("+00:00", "Z"))
            row = indexed.get(key)
            if row is None:
                row = {
                    "city_id": city["id"], "city": city["name"], "province": city["province"],
                    "latitude": city["latitude"], "longitude": city["longitude"],
                    "time_utc": key[1], "ghi_wm2": None, "unit": "W/m²",
                    "source": "not_returned_by_open_meteo", "quality_flag": "missing",
                    "temporal_semantics": "backward_average_native_10min",
                }
            result.append(row)
            timestamp += timedelta(minutes=10)
    return result

def load_existing() -> list[dict[str, Any]]:
    if not LATEST_FILE.exists():
        return []
    try:
        return json.loads(LATEST_FILE.read_text(encoding="utf-8")).get("records", [])
    except (OSError, json.JSONDecodeError):
        LOG.warning("Existing latest.json unreadable; starting fresh")
        return []

def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    cities = json.loads(CITY_FILE.read_text(encoding="utf-8"))
    collected, failures = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_city, city): city for city in cities}
        for future in as_completed(futures):
            city = futures[future]
            try:
                collected.extend(future.result())
            except Exception as exc:
                LOG.exception("Open-Meteo request failed for %s", city["id"])
                failures.append({"city_id": city["id"], "error": str(exc)})
    if not collected:
        raise RuntimeError("No city data returned; refusing to overwrite last good snapshot")
    collected = complete_timeline(collected, cities)
    fallback_this_run = fill_gaps_with_jaxa(collected, cities)
    now, cutoff = utc_now(), utc_now() - timedelta(hours=26)
    merged = {}
    for row in load_existing() + collected:
        if parse_time(row["time_utc"]) >= cutoff:
            key = (row["city_id"], row["time_utc"])
            previous = merged.get(key)
            # Never replace a previously valid observation with a later null/missing row.
            if previous is not None and previous.get("ghi_wm2") is not None and row.get("ghi_wm2") is None:
                continue
            merged[key] = row
    records = sorted(merged.values(), key=lambda r: (r["time_utc"], r["city_id"]))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": now.isoformat().replace("+00:00", "Z"),
               "provider": "Open-Meteo Satellite Radiation API + optional JAXA P-Tree FTP",
               "variable": "shortwave_radiation", "unit": "W/m²",
               "temporal_resolution": "native (Himawari-9: 10 min)", "records": records}
    LATEST_FILE.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    valid = sum(r["ghi_wm2"] is not None for r in records)
    fallback_total = sum(r["source"] == "jaxa_himawari9_l2" for r in records)
    status = {"updated_at_utc": payload["generated_at_utc"], "cities_configured": len(cities),
              "cities_returned": len({r["city_id"] for r in collected}), "records_in_window": len(records),
              "valid_records": valid, "missing_records": len(records)-valid,
              "jaxa_fallback_records_in_window": fallback_total,
              "jaxa_fallback_records_this_run": fallback_this_run, "open_meteo_failures": failures,
              "jaxa_fallback_enabled": bool(os.getenv("H8_USERNAME") and os.getenv("H8_PASSWORD")),
              "notes": ["Open-Meteo values are backward-averaged native 10-minute intervals.",
                        "JAXA L2 fallback values are raw instantaneous scan samples and are not time-equivalent.",
                        "Open-Meteo and JAXA use the same Himawari-9 family; this is not independent-sensor validation."]}
    STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LOG.info("Saved %s records (%s valid, %s JAXA fallback, %s missing)",
             len(records), valid, fallback_total, len(records)-valid)
    if failures:
        LOG.warning("%s city requests failed; previous snapshot rows retained where available", len(failures))

if __name__ == "__main__":
    main()
