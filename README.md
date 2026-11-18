# Grafana Server MCP

Read-only FastMCP server for Grafana metrics (Prometheus) and logs (Loki). It wraps the
[`gcx`](https://github.com/grafana/gcx) CLI, supports several Grafana environments at once
(e.g. `dev`, `stg-west`, `prod`) and runs as a localhost-only Docker/Podman compose service.

Only an allowlist of read-only `gcx` subcommands is ever executed; there are no write tools.

## Tools

Every tool except `list_environments` takes a required `env` (one of the names returned by
`list_environments`); there is no default environment. `datasource` is a UID or exact name
and may be omitted when the environment has exactly one datasource of the needed type.

| Tool | Description |
|---|---|
| `list_environments` | Configured environments (name, url, org_id). Never returns tokens. |
| `list_datasources` | Prometheus and Loki datasources of an environment (uid, name, type, isDefault). |
| `metrics_list_names` | Prometheus metric names, optionally filtered. |
| `metrics_labels` | Prometheus label names, or the values of one label. |
| `metrics_query` | PromQL query: instant by default, range with `since` or `from`/`to`. |
| `logs_labels` | Loki label names, or the values of one label. |
| `logs_query` | LogQL log query, newest first (`limit` default 100, max 1000). |
| `logs_metrics` | LogQL metric query (e.g. `sum by (level) (count_over_time(...[5m]))`). |

Results are compacted and capped (50 000 characters by default) and say when they were
truncated. gcx errors are returned as tool errors with any token scrubbed.

## Quickstart

Prerequisites: Docker or Podman with compose, `uv`, and a working host `gcx` configuration
(one gcx context per Grafana environment, authenticated with a service-account token).

```bash
# 1. Export your gcx contexts and keyring tokens to grafana.env (mode 0600, gitignored)
make env                          # all contexts
make env ARGS="--contexts dev,prod --force"   # a subset / update an existing grafana.env

# 2. Build and start the container (published on 127.0.0.1:8004)
docker compose up -d --build      # or: make up  (Podman: see below)

# 3. Register it with Claude Code
claude mcp add --transport http grafana http://127.0.0.1:8004/mcp
#    ...or with GitHub Copilot CLI (then check with `copilot mcp list`)
copilot mcp add --transport http --timeout 120000 grafana http://127.0.0.1:8004/mcp
```

`mcp_config_http.json` is an equivalent client configuration for other MCP clients.
If host port 8004 is already taken, set `GRAFANA_MCP_PORT` in `.env` (read by compose for
interpolation, not passed to the container) and use that port in the client URL.
Alternatively, copy `grafana.env.example` to `grafana.env`, `chmod 600 grafana.env` and fill
it in by hand. The file and service names match a multi-server compose stack, so
`grafana.env` can be copied there as is.

With `GRAFANA_LOG_PATH` set (the compose default), structured tool logs go to
`/var/log/grafana-mcp/server.log` in the `grafana_logs` volume, not to `make logs`. Read them with:

```bash
docker compose exec grafana-mcp-1 tail -f /var/log/grafana-mcp/server.log
```

Other targets: `make down`, `make logs`, `make docker-build`, `make check` (ruff + mypy + pytest).

## Configuration

Environments are declared by name; each name maps to a variable prefix: the name uppercased
with every non-alphanumeric character replaced by `_` (`stg-west` → `GRAFANA_STG_WEST_*`).

| Variable | Required | Description |
|---|---|---|
| `GRAFANA_ENVIRONMENTS` | Yes | Comma-separated environment names, e.g. `dev,stg-west,prod` |
| `GRAFANA_<PREFIX>_URL` | Yes | Grafana base URL, e.g. `https://grafana.example.com` |
| `GRAFANA_<PREFIX>_TOKEN` | Yes | Service-account token (`glsa_...`) |
| `GRAFANA_<PREFIX>_ORG_ID` | No | Grafana organisation id (default `1`) |
| `GRAFANA_GCX_TIMEOUT` | No | Per-call gcx timeout in seconds (default `60`) |
| `GRAFANA_MAX_CONCURRENCY` | No | Max concurrent gcx processes (default `4`) |
| `GRAFANA_MAX_RESULT_CHARS` | No | Result size cap (default `50000`) |
| `GRAFANA_GCX_PATH` | No | gcx binary (default `gcx`; the image ships gcx 1.7.1) |
| `GRAFANA_LOG_PATH` | No | Structured JSON log file (set by compose to a volume) |
| `GRAFANA_MCP_PORT` | No | Host port for compose (default `8004`, always bound to `127.0.0.1`) |
| `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` | No | Passed through to gcx |
| `GRAFANA_EXTRA_CA_FILE` | No | Host path of an extra PEM CA bundle (compose mounts it) |
| `MCP_TRANSPORT` | No | `stdio` (default outside the image) or `http` |
| `MCP_HTTP_HOST` / `MCP_HTTP_PORT` | No | HTTP bind address (image: `0.0.0.0:8000`) |

### Friendly environment names

The environment names are whatever your gcx contexts are called. If you prefer to talk about
"staging" or "the EU cluster", map those words to environment names in your own Claude skill
or `CLAUDE.md` (kept outside this repository) rather than renaming contexts.

## Local development (without a container)

```bash
uv venv .venv && uv pip install -e ".[dev]"
source .venv/bin/activate
make check
make run-stdio            # or: make run-http  (127.0.0.1:8000)
```

This uses the `gcx` on your `PATH` and the same `GRAFANA_*` variables (read from `grafana.env`, or `.env`).

## Security

- **Localhost only.** The MCP endpoint has no authentication. Compose publishes it on
  `127.0.0.1` only; do not publish the port beyond localhost.
- **Read-only.** Only allowlisted read-only gcx subcommands can run. Still, use
  **Viewer-role service-account tokens** so the credentials themselves cannot write.
- **Tokens.** `grafana.env` is written with mode `0600` and is gitignored and excluded from the
  image build context. Inside the container the tokens live in the environment and in a
  gcx config on a tmpfs; anyone who can run `docker inspect` on your host can read them.
  This is acceptable for a single-user workstation, not for a shared host.
- Tokens are never logged or returned by tools; gcx output is scrubbed of them.

## Troubleshooting

**Works with gcx on the host, but tools fail in the container** (timeouts, DNS or TLS errors):

1. **VPN with an HTTP proxy:** set `HTTPS_PROXY` (and `NO_PROXY`) in `grafana.env`; they are passed to gcx.
2. **TLS-intercepting proxy:** set `GRAFANA_EXTRA_CA_FILE=/path/to/ca.pem` in `.env`. The
   entrypoint appends it to a copy of the system CA bundle and sets `SSL_CERT_FILE`.
3. **Last resort, host networking:** use the commented `network_mode: host` block in
   `docker-compose.yml`. Port publishing does not apply then, so remove `ports:` and keep
   `MCP_HTTP_HOST=127.0.0.1` so the server binds to localhost itself.

Zero-trust clients such as Zscaler Private Access usually work from a container on the
default network without any of the above. The proxy path is best-effort and less tested.

**Container unhealthy / not starting:** `make logs`. A configuration error (e.g. a missing
`GRAFANA_<PREFIX>_TOKEN`) is reported at startup with the variable name, never its value.

## Podman

Rootless Podman works as `docker`: the Dockerfile uses fully qualified image names
(`docker.io/library/python:3.12-slim`) and the healthcheck uses Python rather than curl.
`docker compose` / `podman compose` with `docker-compose` as the provider works out of the
box; a bare `docker-compose` (v1) needs `DOCKER_HOST=unix://$XDG_RUNTIME_DIR/podman/podman.sock`.
The healthcheck is a single `CMD-SHELL` string because Podman splits exec-form arguments on
spaces. In host-network mode the port is the one the server binds (`MCP_HTTP_PORT`).

## Image

Multi-stage build on `python:3.12-slim`, runs as the non-root `mcp` user. gcx is downloaded
from the official `grafana/gcx` GitHub release (`--build-arg GCX_VERSION=1.7.1`) and verified
against the release's `checksums.txt`. The image contains no configuration and no secrets.

## License

MIT
