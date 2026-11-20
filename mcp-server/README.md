# envdata-sat GitHub Actions MCP server

A local STDIO MCP server for Codex. It uses the dedicated GitHub App installation to trigger and monitor only the `realtime.yml` workflow in `StorywithLove/envdata-sat`.

## Security model

- Fixed repository: `StorywithLove/envdata-sat`
- Fixed workflow: `.github/workflows/realtime.yml`
- Fixed branch: `main`
- No arbitrary workflow, repository, branch, file path, or API URL arguments.
- GitHub App installation token is requested with only `actions:write` and `contents:read`, scoped to `envdata-sat`.
- The private key is read from a local file path and must never be committed.
- No inbound network listener; MCP uses local STDIO.

## Prerequisites

- Node.js 20+
- The GitHub App installed on `StorywithLove/envdata-sat`
- App permissions: Actions read/write, Contents read-only, Metadata read-only
- App ID, Installation ID, and downloaded PEM private key

## Install

From this directory:

```powershell
npm install
npm run check
```

Store the PEM outside the repository, for example:

```text
%USERPROFILE%\.config\github-apps\envdata-sat-dispatcher.pem
```

Restrict access to the file to your Windows user. Do not put its contents in this repository, Notion, chat, or a shared folder.

## Configure Codex

Add the following to `%USERPROFILE%\.codex\config.toml`. Replace the key path with the actual local path. App ID and Installation ID are identifiers, not secrets; the PEM file is the sensitive credential.

```toml
[mcp_servers.envdata_sat]
command = "node"
args = ["D:/path/to/envdata-sat/mcp-server/server.mjs"]
startup_timeout_sec = 20
tool_timeout_sec = 60

[mcp_servers.envdata_sat.env]
GITHUB_APP_ID = "5843680"
GITHUB_APP_INSTALLATION_ID = "183919125"
GITHUB_APP_PRIVATE_KEY_PATH = "C:/Users/YOUR_USER/.config/github-apps/envdata-sat-dispatcher.pem"
```

Restart Codex (or the Codex IDE extension), then inspect available MCP tools with `/mcp` or `codex mcp list`.

## Tools

- `trigger_satellite_collection`: starts the fixed workflow on `main`; avoids starting a duplicate if a recent run is already active.
- `get_latest_collection_runs`: lists recent runs.
- `get_collection_run_details`: reads a run's status, jobs, and step conclusions.
- `get_city_radiation_snapshot`: reads the latest city snapshot and status JSON.

## Notes

- A successful dispatch response means GitHub accepted the request, not that the workflow completed successfully.
- Poll run status separately and inspect job/step conclusions before reporting success.
- The snapshot tool reports missing files rather than treating them as successful data.
- This local STDIO transport is the first deployment stage. The same GitHub App logic can later be exposed through an authenticated Streamable HTTP transport for remote clients; do not expose the private key or an unauthenticated dispatch endpoint.
