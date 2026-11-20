# envdata-sat

Independent Himawari satellite-radiation data acquisition, validation, and quick visualization.

The project retrieves native-resolution satellite GHI/SWR data, generates daily CSVs for 31 mainland provincial-level administrative centers, supports custom coordinate/time-range extraction, and provides a lightweight web dashboard.

**No PostgreSQL. No OCI dependency. No manual station-data upload.**

Responsibilities are separated across repositories:

- `envdata-sat`: near-real-time acquisition, daily CSV generation, data-quality reports, and quick visualization.
- `env-archive`: long-term historical CSV archive, synchronized monthly from envdata-sat.
- `envdata-sync`: reference for the lightweight automated synchronization pattern.

See [README_PLATFORM.md](README_PLATFORM.md) for the current architecture, data semantics, workflows, output layout, and local usage.

The remainder of this README preserves background notes from the original single-site Himawari prototype. Those notes describe historical implementation context and should not be interpreted as the current storage/deployment architecture.
