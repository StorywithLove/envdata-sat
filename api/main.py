"""Read-only API over the latest city radiation snapshot."""
from __future__ import annotations
import json, os
from datetime import datetime
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = Path(os.getenv("RADIATION_DATA_FILE", ROOT / "data" / "cities" / "latest.json"))
STATUS_FILE = DATA_FILE.with_name("status.json")
app = FastAPI(title="China Satellite Radiation API", version="0.1.0",
              description="City-level GHI with provenance-aware JAXA L2 fallback.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

def parse_filter_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # Treat timestamps without an explicit offset as UTC, matching the stored data.
    if parsed.tzinfo is None:
        from datetime import timezone
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed

def read_snapshot() -> dict[str, Any]:
    if not DATA_FILE.exists():
        raise HTTPException(status_code=503, detail="No radiation snapshot is available yet")
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail=f"Snapshot is unreadable: {exc}") from exc

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/api/v1/status")
def status() -> dict[str, Any]:
    if not STATUS_FILE.exists():
        return {"status": "waiting_for_first_collection"}
    return json.loads(STATUS_FILE.read_text(encoding="utf-8"))

@app.get("/api/v1/cities")
def cities() -> dict[str, Any]:
    snapshot = read_snapshot()
    latest = {}
    for row in snapshot.get("records", []):
        if row.get("ghi_wm2") is None:
            continue
        previous = latest.get(row["city_id"])
        if previous is None or row["time_utc"] > previous["time_utc"]:
            latest[row["city_id"]] = row
    return {"generated_at_utc": snapshot.get("generated_at_utc"),
            "unit": snapshot.get("unit", "W/m²"), "cities": list(latest.values())}

@app.get("/api/v1/radiation/point")
def radiation_point(city: str = Query(..., description="City id, e.g. wuhan"),
                    start: str | None = Query(None, description="Inclusive ISO-8601 timestamp"),
                    end: str | None = Query(None, description="Inclusive ISO-8601 timestamp")) -> dict[str, Any]:
    snapshot = read_snapshot()
    rows = [r for r in snapshot.get("records", []) if r.get("city_id") == city]
    if not rows:
        raise HTTPException(status_code=404, detail=f"No data found for city '{city}'")
    try:
        start_dt = parse_filter_time(start)
        end_dt = parse_filter_time(end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="start/end must be ISO-8601 timestamps") from exc
    if start_dt:
        rows = [r for r in rows if datetime.fromisoformat(r["time_utc"].replace("Z", "+00:00")) >= start_dt]
    if end_dt:
        rows = [r for r in rows if datetime.fromisoformat(r["time_utc"].replace("Z", "+00:00")) <= end_dt]
    return {"city_id": city, "generated_at_utc": snapshot.get("generated_at_utc"),
            "unit": snapshot.get("unit", "W/m²"), "records": rows}
