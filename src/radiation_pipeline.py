#!/usr/bin/env python3
"""Hourly incremental collector for 31 fixed Himawari stations.

Each run refreshes the current UTC day's daily CSV. During the first three UTC
hours it also refreshes yesterday, allowing late provider publication and
missed runs to be recovered. The CSV is rewritten atomically after timestamp
deduplication; it is not a per-run snapshot.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from .radiation_extract import FIELDS, fetch_location, load_cities, parse_utc_timestamp
except ImportError:  # Direct execution: python src/radiation_pipeline.py
    from radiation_extract import FIELDS, fetch_location, load_cities, parse_utc_timestamp

ROOT = Path(__file__).resolve().parents[1]
DAILY_DIR = ROOT / "data" / "daily"
CITY_DIR = ROOT / "data" / "cities"
LATEST_FILE = CITY_DIR / "latest.json"
SNAPSHOT_STATUS_FILE = CITY_DIR / "status.json"
LOG = logging.getLogger("hourly-radiation-collector")
MODEL = "jma_jaxa_himawari"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)


def daily_paths(day: date) -> tuple[Path, Path]:
    folder = DAILY_DIR / f"{day:%Y}" / f"{day:%m}"
    stem = f"radiation_{day:%Y%m%d}"
    return folder / f"{stem}.csv", folder / f"{stem}.status.json"


def read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda row: (row["time_utc"], row["station_id"]))
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8-sig", newline="", dir=path.parent,
        prefix=path.name + ".", suffix=".tmp", delete=False,
    ) as stream:
        temporary = Path(stream.name)
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def merge_rows(existing: list[dict[str, Any]], fetched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Upsert by station/time; never downgrade a valid value to a missing value."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in existing + fetched:
        key = (row["station_id"], row["time_utc"])
        previous = merged.get(key)
        new_is_missing = row.get("ghi_wm2", "") in ("", None)
        previous_is_valid = previous is not None and previous.get("ghi_wm2", "") not in ("", None)
        if previous_is_valid and new_is_missing:
            continue
        merged[key] = {field: row.get(field, "") for field in FIELDS}
    return sorted(merged.values(), key=lambda row: (row["time_utc"], row["station_id"]))


def is_day_complete(day: date, cities: list[dict[str, Any]]) -> bool:
    csv_path, status_path = daily_paths(day)
    rows = read_csv(csv_path)
    if not status_path.is_file():
        return False
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expected_ids = {city["station_id"] for city in cities}
    if len(rows) != len(expected_ids) * 144:
        return False
    if status.get("locations_succeeded") != len(expected_ids) or status.get("locations_failed") or status.get("failures"):
        return False
    grouped: dict[str, set[str]] = {station_id: set() for station_id in expected_ids}
    for row in rows:
        station_id = row.get("station_id")
        if station_id not in grouped:
            return False
        grouped[station_id].add(row.get("time_utc", ""))
    return all(len(times) == 144 for times in grouped.values())


def summarize_day(
    day: date,
    rows: list[dict[str, Any]],
    cities: list[dict[str, Any]],
    summaries: dict[str, dict[str, Any]],
    failures: list[dict[str, str]],
    generated_at: str,
) -> dict[str, Any]:
    by_station: dict[str, list[dict[str, Any]]] = {city["station_id"]: [] for city in cities}
    for row in rows:
        by_station.setdefault(row["station_id"], []).append(row)

    location_summaries = []
    for city in cities:
        station_id = city["station_id"]
        station_rows = by_station.get(station_id, [])
        api_summary = summaries.get(station_id, {})
        valid = sum(row.get("ghi_wm2", "") not in ("", None) for row in station_rows)
        missing_timestamp = sum(row.get("quality_flag") == "provider_timestamp_missing" for row in station_rows)
        provider_null = sum(row.get("quality_flag") == "provider_returned_null" for row in station_rows)
        location_summaries.append({
            "station_id": station_id,
            "records": len(station_rows),
            "valid_records": valid,
            "missing_timestamp_records": missing_timestamp,
            "provider_null_records": provider_null,
            "cadences_seconds": api_summary.get("cadences_seconds", [600]),
            "observed_time_steps_seconds": api_summary.get("observed_time_steps_seconds", []),
            "cadence_verified": api_summary.get("cadence_verified", False),
            "actual_model": api_summary.get("actual_model"),
            "temporal_semantics": "backward_average_interval_end",
        })

    valid_records = sum(item["valid_records"] for item in location_summaries)
    records = len(rows)
    return {
        "generated_at_utc": generated_at,
        "date_utc": day.isoformat(),
        "period_start_utc": day.isoformat(),
        "period_end_utc": day.isoformat(),
        "locations_requested": len(cities),
        "locations_succeeded": len(cities) - len(failures),
        "locations_failed": failures,
        "failures": failures,
        "records": records,
        "valid_records": valid_records,
        "missing_records": records - valid_records,
        "model_requested": MODEL,
        "temporal_resolution": "native; 600-second UTC interval-end grid",
        "location_summaries": location_summaries,
    }


def fetch_day(day: date, cities: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, str]]]:
    collected: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(fetch_location, city, day, day, MODEL): city
            for city in cities
        }
        for future in as_completed(futures):
            city = futures[future]
            try:
                rows, summary = future.result()
                collected.extend(rows)
                summaries[city["station_id"]] = summary
            except Exception as exc:
                LOG.exception("Provider request failed for %s on %s", city["station_id"], day)
                failures.append({"station_id": city["station_id"], "error": str(exc)})
    return collected, summaries, failures


def dashboard_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "city_id": row["station_id"],
        "city": row["station_name"],
        "province": row["province"],
        "latitude": float(row["latitude"]),
        "longitude": float(row["longitude"]),
        "time_utc": row["time_utc"],
        "ghi_wm2": None if row["ghi_wm2"] in ("", None) else float(row["ghi_wm2"]),
        "unit": row["unit"],
        "source": row["source"],
        "quality_flag": row["quality_flag"],
        "temporal_semantics": "backward_average_interval_end",
    }


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    now = utc_now()
    today = now.date()
    # Only completed 10-minute interval-end labels are eligible for the current day.
    latest_completed = now.replace(minute=(now.minute // 10) * 10)
    cities = load_cities()

    days = [today]
    # Re-fetch yesterday only if its daily CSV is incomplete. This catches a
    # missed final-hour run without doubling API requests on normal days.
    yesterday = today - timedelta(days=1)
    if not is_day_complete(yesterday, cities):
        days.insert(0, yesterday)

    all_daily_rows: dict[date, list[dict[str, Any]]] = {}
    current_day_status: dict[str, Any] | None = None
    for day in days:
        fetched, summaries, failures = fetch_day(day, cities)
        csv_path, status_path = daily_paths(day)
        existing = read_csv(csv_path)
        if day == today:
            fetched = [row for row in fetched if parse_utc_timestamp(row["time_utc"]) <= latest_completed]
        merged = merge_rows(existing, fetched)
        if not merged:
            if failures:
                LOG.error("No records available for %s; preserving any existing files", day)
                continue
            raise RuntimeError(f"No records produced for {day}")
        write_csv_atomic(csv_path, merged)
        generated_at = now.isoformat().replace("+00:00", "Z")
        status = summarize_day(day, merged, cities, summaries, failures, generated_at)
        atomic_json(status_path, status)
        all_daily_rows[day] = merged
        if day == today:
            current_day_status = status
        LOG.info(
            "%s: %s records, %s valid, %s provider failures",
            day, status["records"], status["valid_records"], len(failures),
        )

    # Keep the dashboard's lightweight rolling 24-hour view; the daily CSVs
    # remain the authoritative history.
    cutoff = now - timedelta(hours=24)
    latest_rows = []
    for day in [today - timedelta(days=1), today]:
        csv_path, _ = daily_paths(day)
        source_rows = all_daily_rows.get(day, read_csv(csv_path))
        for row in source_rows:
            if parse_utc_timestamp(row["time_utc"]) >= cutoff:
                latest_rows.append(dashboard_record(row))
    latest_rows.sort(key=lambda row: (row["time_utc"], row["city_id"]))
    payload = {
        "generated_at_utc": now.isoformat().replace("+00:00", "Z"),
        "provider": "Open-Meteo Satellite Radiation API",
        "variable": "shortwave_radiation",
        "unit": "W/m²",
        "temporal_resolution": "native Himawari-9 10-minute intervals",
        "records": latest_rows,
    }
    atomic_json(LATEST_FILE, payload)
    if current_day_status is not None:
        atomic_json(SNAPSHOT_STATUS_FILE, current_day_status)


if __name__ == "__main__":
    main()
