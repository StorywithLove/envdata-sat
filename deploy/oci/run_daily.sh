#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/envdata-sat}"
RADIATION_DATA_DIR="${RADIATION_DATA_DIR:-/var/lib/envdata-sat}"

mkdir -p "$RADIATION_DATA_DIR"
git -C "$APP_DIR" pull --ff-only origin main
cd "$APP_DIR"
export RADIATION_DATA_DIR
docker compose -f compose.yaml run --build --rm radiation-archive cities
