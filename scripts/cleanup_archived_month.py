#!/usr/bin/env python3
"""Delete one expired source month only after matching an archive manifest."""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import re
from datetime import date
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cleanup_month(month: str, manifest_path: Path, data_root: Path) -> list[Path]:
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("month must use YYYY-MM format")
    year, month_number = map(int, month.split("-"))
    if not 1 <= month_number <= 12:
        raise ValueError("month must use YYYY-MM format")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("month") != month:
        raise ValueError("Archive manifest month does not match requested cleanup month")
    expected_days = calendar.monthrange(year, month_number)[1]
    days = manifest.get("days", [])
    if manifest.get("expected_days") != expected_days or manifest.get("archived_days") != expected_days or len(days) != expected_days:
        raise ValueError("Archive manifest does not prove a complete calendar month")

    source_dir = data_root / f"{year:04d}" / f"{month_number:02d}"
    verified: list[tuple[Path, Path]] = []
    seen_dates: set[str] = set()
    for item in days:
        day_string = item.get("date_utc", "")
        day = date.fromisoformat(day_string)
        if day.strftime("%Y-%m") != month or day_string in seen_dates:
            raise ValueError(f"Unexpected or duplicate manifest date: {day_string}")
        seen_dates.add(day_string)
        compact = day.strftime("%Y%m%d")
        csv_path = source_dir / f"radiation_{compact}.csv"
        status_path = source_dir / f"radiation_{compact}.status.json"
        if not csv_path.is_file() or not status_path.is_file():
            raise FileNotFoundError(f"Source CSV or status missing for {day_string}")
        if sha256(csv_path) != item.get("csv", {}).get("sha256"):
            raise ValueError(f"CSV SHA-256 does not match archived manifest for {day_string}")
        if sha256(status_path) != item.get("status", {}).get("sha256"):
            raise ValueError(f"Status SHA-256 does not match archived manifest for {day_string}")
        verified.append((csv_path, status_path))

    expected_date_strings = {
        date(year, month_number, day).isoformat()
        for day in range(1, expected_days + 1)
    }
    if seen_dates != expected_date_strings:
        raise ValueError("Archive manifest does not contain every date in the month")

    # No source file is removed until every file in the month has passed verification.
    removed: list[Path] = []
    for csv_path, status_path in verified:
        csv_path.unlink()
        status_path.unlink()
        removed.extend([csv_path, status_path])
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path("data/daily"))
    args = parser.parse_args()
    removed = cleanup_month(args.month, args.manifest, args.data_root)
    print(f"Verified archive manifest and removed {len(removed)} source files for {args.month}")


if __name__ == "__main__":
    main()
