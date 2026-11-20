import fs from "node:fs/promises";
import crypto from "node:crypto";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const OWNER = "StorywithLove";
const REPO = "envdata-sat";
const WORKFLOW = "realtime.yml";
const REF = "main";
const API = "https://api.github.com";
const API_VERSION = "2022-11-28";

function requiredEnv(name) {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`Missing required environment variable: ${name}`);
  return value;
}

const appId = requiredEnv("GITHUB_APP_ID");
const installationId = requiredEnv("GITHUB_APP_INSTALLATION_ID");
const privateKeyPath = requiredEnv("GITHUB_APP_PRIVATE_KEY_PATH");
let cachedToken = null;
let tokenExpiresAt = 0;

function base64url(value) {
  return Buffer.from(value).toString("base64url");
}

async function createAppJwt() {
  const privateKey = await fs.readFile(privateKeyPath, "utf8");
  const now = Math.floor(Date.now() / 1000);
  const header = base64url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const payload = base64url(JSON.stringify({
    iat: now - 60,
    exp: now + 8 * 60,
    iss: appId
  }));
  const unsigned = `${header}.${payload}`;
  const signer = crypto.createSign("RSA-SHA256");
  signer.update(unsigned);
  signer.end();
  const signature = signer.sign(privateKey).toString("base64url");
  return `${unsigned}.${signature}`;
}

async function getInstallationToken() {
  if (cachedToken && Date.now() < tokenExpiresAt - 60_000) return cachedToken;

  const jwt = await createAppJwt();
  const response = await fetch(
    `${API}/app/installations/${installationId}/access_tokens`,
    {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${jwt}`,
        "X-GitHub-Api-Version": API_VERSION,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        repositories: [REPO],
        permissions: { actions: "write", contents: "read" }
      })
    }
  );

  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`GitHub App token request failed (${response.status}): ${JSON.stringify(body)}`);
  }

  cachedToken = body.token;
  tokenExpiresAt = Date.parse(body.expires_at);
  return cachedToken;
}

async function github(path, options = {}) {
  const token = await getInstallationToken();
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": API_VERSION,
      ...(options.headers || {})
    }
  });

  if (response.status === 204) return null;
  const text = await response.text();
  let body;
  try { body = text ? JSON.parse(text) : null; }
  catch { body = text; }

  if (!response.ok) {
    throw new Error(`GitHub API request failed (${response.status}): ${JSON.stringify(body)}`);
  }
  return body;
}

function result(data) {
  return {
    content: [{ type: "text", text: JSON.stringify(data, null, 2) }]
  };
}

const server = new McpServer({
  name: "envdata-sat-github-actions",
  version: "0.1.0"
});

server.tool(
  "trigger_satellite_collection",
  "Trigger the fixed envdata-sat realtime.yml workflow on main. Refuses to create duplicate runs while a previous run is queued or in progress. This operation starts a real GitHub Actions run.",
  {},
  async () => {
    const runs = await github(
      `/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/runs?branch=${REF}&per_page=10`
    );
    const active = (runs.workflow_runs || []).find(run =>
      ["queued", "in_progress", "waiting", "requested", "pending"].includes(run.status)
    );

    if (active) {
      return result({
        triggered: false,
        reason: "An existing collection run is already active; no duplicate was started.",
        run_id: active.id,
        status: active.status,
        html_url: active.html_url
      });
    }

    await github(
      `/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ref: REF })
      }
    );

    return result({
      triggered: true,
      workflow: WORKFLOW,
      repository: `${OWNER}/${REPO}`,
      ref: REF,
      message: "Dispatch accepted by GitHub. Use get_latest_collection_runs to obtain the run ID and monitor it."
    });
  }
);

server.tool(
  "get_latest_collection_runs",
  "List the latest envdata-sat realtime collection workflow runs on main.",
  { per_page: z.number().int().min(1).max(20).default(5) },
  async ({ per_page }) => {
    const data = await github(
      `/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/runs?branch=${REF}&per_page=${per_page}`
    );
    return result((data.workflow_runs || []).map(run => ({
      id: run.id,
      run_number: run.run_number,
      status: run.status,
      conclusion: run.conclusion,
      created_at: run.created_at,
      updated_at: run.updated_at,
      html_url: run.html_url,
      head_sha: run.head_sha
    })));
  }
);

server.tool(
  "get_collection_run_details",
  "Get status, job summaries, and step conclusions for one envdata-sat collection run.",
  { run_id: z.number().int().positive() },
  async ({ run_id }) => {
    const [run, jobsData] = await Promise.all([
      github(`/repos/${OWNER}/${REPO}/actions/runs/${run_id}`),
      github(`/repos/${OWNER}/${REPO}/actions/runs/${run_id}/jobs?per_page=50`)
    ]);

    const jobs = (jobsData.jobs || []).map(job => ({
      id: job.id,
      name: job.name,
      status: job.status,
      conclusion: job.conclusion,
      started_at: job.started_at,
      completed_at: job.completed_at,
      html_url: job.html_url,
      steps: (job.steps || []).map(step => ({
        name: step.name,
        status: step.status,
        conclusion: step.conclusion
      }))
    }));

    return result({
      id: run.id,
      run_number: run.run_number,
      status: run.status,
      conclusion: run.conclusion,
      created_at: run.created_at,
      updated_at: run.updated_at,
      html_url: run.html_url,
      jobs
    });
  }
);

server.tool(
  "get_city_radiation_snapshot",
  "Read the latest city radiation snapshot and collection status JSON from envdata-sat. This tool only reads these two fixed files.",
  {},
  async () => {
    const paths = [
      "data/cities/latest.json",
      "data/cities/status.json"
    ];
    const output = {};

    for (const path of paths) {
      try {
        const response = await fetch(
          `${API}/repos/${OWNER}/${REPO}/contents/${path}?ref=${REF}`,
          {
            headers: {
              Accept: "application/vnd.github.raw+json",
              Authorization: `Bearer ${await getInstallationToken()}`,
              "X-GitHub-Api-Version": API_VERSION
            }
          }
        );
        const text = await response.text();
        if (response.status === 404) {
          output[path] = { available: false, message: "File not found yet." };
        } else if (!response.ok) {
          output[path] = { available: false, error: `GitHub returned ${response.status}: ${text.slice(0, 500)}` };
        } else {
          try { output[path] = { available: true, data: JSON.parse(text) }; }
          catch { output[path] = { available: true, raw: text.slice(0, 20000) }; }
        }
      } catch (error) {
        output[path] = { available: false, error: error.message };
      }
    }

    return result(output);
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
