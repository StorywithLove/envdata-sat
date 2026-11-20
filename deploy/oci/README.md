# OCI persistent archive worker

This is a small, database-free worker for the daily fixed-city CSV archive. It stores output on an OCI persistent filesystem and syncs application code from the public GitHub `main` branch before each run.

## Prerequisites

- Oracle Linux 9.x instance with outbound HTTPS access.
- Git.
- Docker Engine and the Docker Compose plugin (`docker compose`).
- A persistent filesystem path with enough free space, e.g. `/var/lib/envdata-sat`.

No inbound port, public IP exposure, GitHub token, GitHub App key, PostgreSQL, JAXA credential, or other secret is required for this daily Open-Meteo archive job.

## One-time setup

```bash
sudo dnf install -y git
sudo git clone https://github.com/StorywithLove/envdata-sat.git /opt/envdata-sat
sudo mkdir -p /var/lib/envdata-sat /etc/envdata-sat
sudo cp /opt/envdata-sat/deploy/oci/archive.env.example /etc/envdata-sat/archive.env
sudo chmod 600 /etc/envdata-sat/archive.env
sudo cp /opt/envdata-sat/deploy/oci/envdata-sat-archive.service /etc/systemd/system/
sudo cp /opt/envdata-sat/deploy/oci/envdata-sat-archive.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now envdata-sat-archive.timer
```

Install Docker Engine and its Compose plugin using the Oracle Linux-compatible installation method before enabling the timer. The repository does not change firewall rules or install Docker automatically.

## Verify

```bash
sudo systemctl list-timers envdata-sat-archive.timer
sudo systemctl start envdata-sat-archive.service
sudo journalctl -u envdata-sat-archive.service -n 100 --no-pager
find /var/lib/envdata-sat/daily -type f
```

The timer runs at 01:30 UTC with up to five minutes randomized delay. `Persistent=true` lets systemd run a missed daily task after the host comes back online.

## Custom extraction

Place a coordinate CSV in `/var/lib/envdata-sat/requests/`, then run:

```bash
cd /opt/envdata-sat
sudo docker compose -f compose.yaml run --build --rm radiation-archive custom \
  --coordinates-csv /app/data/requests/sites.csv \
  --start-date 2026-11-01 --end-date 2026-11-20 \
  --output /app/data/custom/request_001.csv
```

CSV and sidecar status JSON are written to the persistent volume. No database is involved.

## Operational notes

- The worker performs `git pull --ff-only origin main` before each daily run and rebuilds the small image, so code updates are picked up automatically.
- Data lives outside the Git checkout. A failed Git sync or failed API request is visible in systemd logs.
- This service only makes outbound requests; it does not expose a network listener.
- Back up `/var/lib/envdata-sat` independently if the archive is important.
