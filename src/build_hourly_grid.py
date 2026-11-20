#!/usr/bin/env python3
"""Build one China-window hourly GHI GeoTIFF/COG from JAXA Himawari-9 L2 scans.

The output is an approximate hourly mean of available instantaneous L2 scans.
It is not a scan-time-corrected product equivalent to Open-Meteo's interval mean.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import rasterio
from netCDF4 import Dataset
from rasterio.transform import from_origin
from rasterio.shutil import copy as raster_copy

LOG = logging.getLogger("hourly-grid")
FTP_ROOT = "ftp://ftp.ptree.jaxa.jp/pub/himawari/L2/PAR/021"
CHINA_BOUNDS = {"west": 70.0, "east": 140.0, "south": 15.0, "north": 55.0}
NODATA = -9999.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="UTC date YYYYMMDD; default: latest completed hour")
    parser.add_argument("--hour", type=int, help="UTC hour 0-23; default: latest completed hour")
    parser.add_argument("--output-dir", default="data/grid/hourly")
    parser.add_argument("--min-scans", type=int, default=4,
                        help="Minimum valid scans per pixel (default: 4 of 6)")
    return parser.parse_args()


def target_hour(args: argparse.Namespace) -> datetime:
    if args.date is not None or args.hour is not None:
        if args.date is None or args.hour is None or not 0 <= args.hour <= 23:
            raise ValueError("Specify both --date YYYYMMDD and --hour 0..23")
        return datetime.strptime(f"{args.date}{args.hour:02d}", "%Y%m%d%H").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)


def filename(scan_time: datetime) -> str:
    return f"H09_{scan_time:%Y%m%d_%H%M}_RFL021_FLDK.02801_02401.nc"


def download_scan(scan_time: datetime, target: Path) -> bool:
    username, password = os.getenv("H8_USERNAME"), os.getenv("H8_PASSWORD")
    if not username or not password:
        raise RuntimeError("Set H8_USERNAME and H8_PASSWORD for JAXA P-Tree FTP")
    name = filename(scan_time)
    remote = f"{FTP_ROOT}/{scan_time:%Y%m}/{scan_time:%d}/{scan_time:%H}/{name}"
    command = [
        "wget", "-q", "--timeout=30", "--tries=2",
        f"--ftp-user={username}", f"--ftp-password={password}",
        "-O", str(target), remote,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        LOG.warning("FTP timeout: %s", name)
        target.unlink(missing_ok=True)
        return False
    if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        LOG.warning("FTP scan unavailable: %s", name)
        target.unlink(missing_ok=True)
        return False
    return True


def read_china_window(path: Path):
    with Dataset(path) as ds:
        lat = np.asarray(ds.variables["latitude"][:], dtype=np.float64)
        lon = np.asarray(ds.variables["longitude"][:], dtype=np.float64)
        swr = ds.variables["SWR"][:]  # netCDF4 applies scale_factor/add_offset and masks fill values
        lat_idx = np.where((lat >= CHINA_BOUNDS["south"]) & (lat <= CHINA_BOUNDS["north"]))[0]
        lon_idx = np.where((lon >= CHINA_BOUNDS["west"]) & (lon <= CHINA_BOUNDS["east"]))[0]
        if not len(lat_idx) or not len(lon_idx):
            raise ValueError("JAXA grid does not intersect the configured China bounding box")
        # Normalize raster orientation: north-to-south rows, west-to-east columns.
        lat_idx = lat_idx[np.argsort(lat[lat_idx])[::-1]]
        lon_idx = lon_idx[np.argsort(lon[lon_idx])]
        window = np.ma.asarray(swr[np.ix_(lat_idx, lon_idx)], dtype=np.float32)
        return lat[lat_idx], lon[lon_idx], window


def write_cog(hour: datetime, total: np.ndarray, count: np.ndarray,
              lat: np.ndarray, lon: np.ndarray, output_dir: Path,
              scans_available: list[str], min_scans: int) -> Path:
    valid = count >= min_scans
    result = np.full(total.shape, NODATA, dtype=np.float32)
    result[valid] = (total[valid] / count[valid]).astype(np.float32)

    dx = float(np.median(np.diff(lon)))
    dy = float(np.median(np.abs(np.diff(lat))))
    transform = from_origin(float(lon[0]) - dx / 2, float(lat[0]) + dy / 2, dx, dy)

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"ghi_{hour:%Y%m%d_%H}00Z"
    final_path = output_dir / f"{stem}.tif"
    temp_path = output_dir / f"{stem}.tmp.tif"
    try:
        with rasterio.open(
            temp_path, "w", driver="GTiff", width=result.shape[1], height=result.shape[0],
            count=1, dtype="float32", crs="EPSG:4326", transform=transform,
            nodata=NODATA, tiled=True, blockxsize=512, blockysize=512,
            compress="DEFLATE", predictor=3,
        ) as dst:
            dst.write(result, 1)
            dst.set_band_description(1, "Hourly mean shortwave radiation (GHI)")
            dst.update_tags(
                variable="shortwave_radiation", units="W/m2",
                temporal_semantics="approximate_hourly_mean_of_instantaneous_L2_scans",
                source="JAXA Himawari-9 L2 PAR SWR",
                scans_available=str(len(scans_available)), scans=",".join(scans_available),
                min_valid_scans_per_pixel=str(min_scans),
            )
        try:
            raster_copy(
                temp_path, final_path, driver="COG", compress="DEFLATE",
                blocksize=512, overview_resampling="average",
            )
        except Exception as exc:
            LOG.warning("COG driver unavailable (%s); keeping tiled compressed GeoTIFF", exc)
            temp_path.replace(final_path)
    finally:
        temp_path.unlink(missing_ok=True)

    metadata = {
        "time_start_utc": hour.isoformat().replace("+00:00", "Z"),
        "time_end_utc": (hour + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "variable": "GHI", "unit": "W/m²",
        "source": "JAXA Himawari-9 L2 PAR SWR",
        "temporal_semantics": "approximate_hourly_mean_of_instantaneous_L2_scans",
        "scans_expected": 6, "scans_available": scans_available,
        "min_valid_scans_per_pixel": min_scans,
        "valid_pixel_fraction": float(valid.mean()),
        "bounds": CHINA_BOUNDS,
        "raster": final_path.name,
        "warning": "Not scan-time corrected; not directly equivalent to Open-Meteo backward-averaged GHI.",
    }
    final_path.with_suffix(".json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return final_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    if not 1 <= args.min_scans <= 6:
        raise ValueError("--min-scans must be between 1 and 6")
    hour = target_hour(args)
    output_dir = Path(args.output_dir) / hour.strftime("%Y/%m/%d")
    stem = f"ghi_{hour:%Y%m%d_%H}00Z"
    final_path = output_dir / f"{stem}.tif"
    if final_path.exists():
        LOG.info("Output already exists, skip: %s", final_path)
        return

    total = count = lat = lon = None
    scans_available = []
    with tempfile.TemporaryDirectory(prefix="jaxa-hour-") as temp:
        for minute in range(0, 60, 10):
            scan_time = hour.replace(minute=minute)
            scan_name = filename(scan_time)
            nc_path = Path(temp) / scan_name
            if not download_scan(scan_time, nc_path):
                continue
            try:
                scan_lat, scan_lon, data = read_china_window(nc_path)
            except Exception:
                LOG.exception("Failed reading %s", scan_name)
                continue
            finally:
                nc_path.unlink(missing_ok=True)

            if total is None:
                lat, lon = scan_lat, scan_lon
                total = np.zeros(data.shape, dtype=np.float64)
                count = np.zeros(data.shape, dtype=np.uint8)
            elif not np.array_equal(lat, scan_lat) or not np.array_equal(lon, scan_lon):
                raise ValueError(f"Grid coordinates changed in {scan_name}")

            valid = ~np.ma.getmaskarray(data) & np.isfinite(np.asarray(data.filled(np.nan)))
            values = np.asarray(data.filled(0), dtype=np.float32)
            total[valid] += values[valid]
            count[valid] += 1
            scans_available.append(scan_name)

    if len(scans_available) < args.min_scans:
        raise RuntimeError(
            f"Only {len(scans_available)}/6 scans available; need at least {args.min_scans}. "
            "No hourly raster written."
        )
    output = write_cog(hour, total, count, lat, lon, output_dir, scans_available, args.min_scans)
    LOG.info("Wrote %s from %s/6 scans", output, len(scans_available))


if __name__ == "__main__":
    main()
