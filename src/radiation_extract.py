#!/usr/bin/env python3
"""Native-resolution satellite GHI extraction for fixed and arbitrary locations.

Examples:
  python src/radiation_extract.py cities --date 2026-11-19
  python src/radiation_extract.py custom --coordinates-csv sites.csv \
      --start-date 2026-11-01 --end-date 2026-11-20 --output custom.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
CITY_FILE = ROOT / "config" / "cities.json"
API_URL = "https://satellite-api.open-meteo.com/v1/archive"
FIELDS = [
    "station_id", "station_name", "province", "latitude", "longitude",
    "time_utc", "ghi_wm2", "unit", "model_requested",
    "temporal_resolution_seconds", "source", "quality_flag",
]
LOG = logging.getLogger("radiation-extract")


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date {value!r}; expected YYYY-MM-DD") from exc


def validate_native_cadence(times: list[str], strict_10min: bool) -> list[int]:
    parsed = [datetime.fromisoformat(t.replace("Z", "+00:00")) for t in times]
    deltas = [int((right - left).total_seconds()) for left, right in zip(parsed, parsed[1:])]
    if not deltas:
        raise ValueError("API returned fewer than two timestamps")
    allowed = {600} if strict_10min else {600, 900, 1800}
    invalid = [(index, delta) for index, delta in enumerate(deltas) if delta not in allowed]
    if invalid:
        raise ValueError(f"Unexpected native cadence intervals: {invalid[:10]}; allowed seconds={sorted(allowed)}")
    # First record uses the first observed interval; every row carries its interval cadence.
    return [deltas[0], *deltas]

def fetch_location(
    location: dict[str, Any], start: date, end: date, model: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    strict_10min = model == "jma_jaxa_himawari"
    daily_city_window = strict_10min and start == end
    api_end = end + timedelta(days=1) if daily_city_window else end
    params = {
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "hourly": "shortwave_radiation",
        "models": model,
        "temporal_resolution": "native",
        "start_date": start.isoformat(),
        "end_date": api_end.isoformat(),
        "timezone": "UTC",
    }
    response = requests.get(API_URL, params=params, timeout=90)
    response.raise_for_status()
    payload = response.json()
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    values = hourly.get("shortwave_radiation") or []
    if not times or len(times) != len(values):
        raise ValueError(
            f"{location['station_id']}: invalid API arrays "
            f"(times={len(times)}, values={len(values)})"
        )
    if daily_city_window:
        day_start = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        selected = [
            (timestamp, value) for timestamp, value in zip(times, values)
            if day_start < datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone.utc) <= day_end
        ]
        times = [item[0] for item in selected]
        values = [item[1] for item in selected]
        first = datetime.fromisoformat(times[0].replace("Z", "+00:00")).astimezone(timezone.utc) if times else None
        last = datetime.fromisoformat(times[-1].replace("Z", "+00:00")).astimezone(timezone.utc) if times else None
        expected_first = day_start + timedelta(minutes=10)
        if len(times) != 144 or first != expected_first or last != day_end:
            raise ValueError("{}: expected 144 backward-averaged labels from {} to {}; received {} from {} to {}".format(location["station_id"], expected_first.isoformat(), day_end.isoformat(), len(times), first.isoformat() if first else "none", last.isoformat() if last else "none"))
    cadences = validate_native_cadence(times, strict_10min=strict_10min)
    actual_model = payload.get("model") or model
    rows = []
    for timestamp, value, cadence in zip(times, values, cadences):
        parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed_timestamp.tzinfo is None:
            parsed_timestamp = parsed_timestamp.replace(tzinfo=timezone.utc)
        rows.append({
            "station_id": location["station_id"],
            "station_name": location["station_name"],
            "province": location.get("province", ""),
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "time_utc": parsed_timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "ghi_wm2": "" if value is None else float(value),
            "unit": "W/m²",
            "model_requested": model,
            "temporal_resolution_seconds": cadence,
            "source": actual_model,
            "quality_flag": "missing" if value is None else "valid",
        })
    summary = {
        "station_id": location["station_id"],
        "records": len(rows),
        "valid_records": sum(row["ghi_wm2"] != "" for row in rows),
        "cadences_seconds": sorted(set(cadences)),
        "actual_model": actual_model,
        "temporal_semantics": "backward_average_interval_end" if daily_city_window else "provider_native_timestamp_semantics_not_normalized",
        "period_start_utc": start.isoformat(),
        "period_end_utc": end.isoformat(),
    }
    return rows, summary


def load_cities() -> list[dict[str, Any]]:
    cities = json.loads(CITY_FILE.read_text(encoding="utf-8"))
    return [{
        "station_id": city["id"],
        "station_name": city["name"],
        "province": city["province"],
        "latitude": city["latitude"],
        "longitude": city["longitude"],
    } for city in cities]


def load_coordinates(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"latitude", "longitude"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("Coordinate CSV must contain latitude,longitude columns")
        locations = []
        for index, raw in enumerate(reader, start=1):
            lat, lon = float(raw["latitude"]), float(raw["longitude"])
            if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
                raise ValueError(f"Coordinate row {index}: latitude/longitude out of range")
            station_id = (raw.get("station_id") or f"point_{index:03d}").strip()
            locations.append({
                "station_id": station_id,
                "station_name": (raw.get("station_name") or station_id).strip(),
                "province": (raw.get("province") or "").strip(),
                "latitude": lat,
                "longitude": lon,
            })
    if not locations:
        raise ValueError("Coordinate CSV contains no locations")
    if len(locations) > 100:
        raise ValueError("A single extraction is limited to 100 locations")
    ids = [item["station_id"] for item in locations]
    if len(ids) != len(set(ids)):
        raise ValueError("station_id values must be unique")
    return locations


def write_outputs(rows: list[dict[str, Any]], summary: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda row: (row["time_utc"], row["station_id"]))
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    status_path = output.with_suffix(".status.json")
    status_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run(args: argparse.Namespace) -> int:
    if args.mode == "cities":
        day = args.date or (datetime.now(timezone.utc).date() - timedelta(days=1))
        start = end = day
        locations = load_cities()
        model = "jma_jaxa_himawari"
        output = args.output or ROOT / "data" / "daily" / f"{day:%Y}" / f"{day:%m}" / f"radiation_{day:%Y%m%d}.csv"
    else:
        if not args.coordinates_csv or not args.start_date or not args.end_date:
            raise ValueError("custom mode requires --coordinates-csv, --start-date and --end-date")
        start, end = args.start_date, args.end_date
        if end < start:
            raise ValueError("--end-date must be on or after --start-date")
        if (end - start).days > 365:
            raise ValueError("A single custom extraction is limited to 366 calendar days")
        locations = load_coordinates(args.coordinates_csv)
        model = "satellite_radiation_seamless"
        output = args.output or Path("custom_radiation.csv")

    all_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=min(6, len(locations))) as pool:
        futures = {
            pool.submit(fetch_location, location, start, end, model): location
            for location in locations
        }
        for future in as_completed(futures):
            location = futures[future]
            try:
                rows, item_summary = future.result()
                all_rows.extend(rows)
                summaries.append(item_summary)
                LOG.info("%s: %s records, %s valid, cadences=%s",
                         location["station_id"], item_summary["records"],
                         item_summary["valid_records"], item_summary["cadences_seconds"])
            except Exception as exc:
                LOG.exception("Extraction failed for %s", location["station_id"])
                failures.append({"station_id": location["station_id"], "error": str(exc)})

    if not all_rows:
        raise RuntimeError("All location requests failed; no CSV written")
    valid_count = sum(row["ghi_wm2"] != "" for row in all_rows)
    status = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "period_start_utc": start.isoformat(),
        "period_end_utc": end.isoformat(),
        "locations_requested": len(locations),
        "locations_succeeded": len(summaries),
        "locations_failed": failures,
        "records": len(all_rows),
        "valid_records": valid_count,
        "missing_records": len(all_rows) - valid_count,
        "model_requested": model,
        "temporal_resolution": "native; validated per record interval",
        "location_summaries": sorted(summaries, key=lambda item: item["station_id"]),
        "failures": failures,
        "storage_note": "CSV output is written to the configured filesystem; no database is required.",
    }
    write_outputs(all_rows, status, Path(output))
    LOG.info("Wrote %s rows to %s (%s valid)", len(all_rows), output, valid_count)
    return 2 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("cities", "custom"))
    parser.add_argument("--date", type=parse_date, help="UTC date for fixed-city daily archive")
    parser.add_argument("--coordinates-csv", type=Path, help="CSV with latitude,longitude and optional station_id,station_name")
    parser.add_argument("--start-date", type=parse_date)
    parser.add_argument("--end-date", type=parse_date)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(run(args))


if __name__ == "__main__":
    main()
