#!/usr/bin/env python3
"""Compare Satellite Radiation API endpoint/model combinations for live cadence diagnostics."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = "https://satellite-api.open-meteo.com/v1"
LAT, LON = 30.5928, 114.3055


def probe(name: str, endpoint: str, model: str, time_params: dict[str, str]) -> dict:
    params = {
        "latitude": LAT,
        "longitude": LON,
        "hourly": "shortwave_radiation",
        "models": model,
        "temporal_resolution": "native",
        "timezone": "UTC",
        **time_params,
    }
    result = {"name": name, "url": f"{BASE}/{endpoint}", "model": model, "params": params}
    try:
        response = requests.get(f"{BASE}/{endpoint}", params=params, timeout=90)
        result["http_status"] = response.status_code
        response.raise_for_status()
        payload = response.json()
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        values = hourly.get("shortwave_radiation") or []
        deltas = [
            int((datetime.fromisoformat(b.replace("Z", "+00:00")) -
                 datetime.fromisoformat(a.replace("Z", "+00:00"))).total_seconds())
            for a, b in zip(times, times[1:])
        ]
        nonnull = [(t, v) for t, v in zip(times, values) if v is not None]
        result.update({
            "time_count": len(times),
            "value_count": len(values),
            "nonnull_count": len(nonnull),
            "null_count": len(values) - len(nonnull),
            "cadence_seconds": dict(Counter(deltas)),
            "first_times": list(zip(times[:8], values[:8])),
            "last_times": list(zip(times[-12:], values[-12:])),
            "nonnull_first": nonnull[:5],
            "nonnull_last": nonnull[-5:],
            "api_error": payload.get("reason"),
        })
    except Exception as exc:
        result["error"] = str(exc)
    return result


def main() -> None:
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    date_range = {"start_date": yesterday.isoformat(), "end_date": today.isoformat()}
    rolling = {"past_hours": "25", "forecast_hours": "1"}
    cases = [
        probe("archive_himawari_date_range", "archive", "jma_jaxa_himawari", date_range),
        probe("archive_seamless_date_range", "archive", "satellite_radiation_seamless", date_range),
        probe("forecast_himawari_rolling", "forecast", "jma_jaxa_himawari", rolling),
        probe("forecast_seamless_rolling", "forecast", "satellite_radiation_seamless", rolling),
    ]
    output = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "cases": cases}
    Path("probe.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for item in cases:
        print(json.dumps({k: item.get(k) for k in (
            "name", "http_status", "time_count", "nonnull_count", "null_count",
            "cadence_seconds", "error", "api_error"
        )}, ensure_ascii=False))


if __name__ == "__main__":
    main()
