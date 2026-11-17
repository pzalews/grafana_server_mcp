# Design: Grafana MCP (gcx-backed, read-only)

**Date:** 2026-11-17
**Status:** Approved 2026-11-17

## Intent

A Python MCP server that lets Claude answer operational questions against **several Grafana instances** using the installed **`gcx`** CLI, for example:

- "How many 500 errors were there in the last day on stg?" (Prometheus/PromQL)
- "Show me the latest errors in the istio gateway logs on prod." (Loki/LogQL)

It is a sibling of `bitbucket_server_mcp`, `conflu_serwer_mcp` and `jira_server_mcp` and **mirrors `bitbucket_server_mcp`** in layout, tooling and conventions. It runs as a **long-running Docker Compose service over streamable HTTP**.

**Why gcx and not raw HTTP:** gcx already knows the user's Grafana stacks, handles datasource query APIs (Prometheus, Loki), time parsing and output shaping. The server is a thin, safe wrapper around a fixed set of gcx read commands.

## The repository is public

This is a hard constraint that shapes everything:

- **No organisation-specific information anywhere** in code, tests, fixtures, README, examples, commit messages or this repo's docs: no company names, stack/context names, Grafana URLs, datasource UIDs, label values.
- Examples use generic names (`dev`, `stg`, `prod`) and `https://grafana.example.com`.
- Mapping "stg"/"prod" to actual environments is **not** the server's job; the user handles that in a private Claude skill. The server only reports which environments exist.
- `.env` (holding real URLs and tokens) is gitignored; only `.env.example` with placeholders is committed.

## Goals

- Read-only access to **metrics** (Prometheus) and **logs** (Loki) on any configured Grafana environment.
- Discovery tools so Claude can find the right datasource, metric names and labels before writing a query, instead of guessing.
- An `list_environments` tool listing configured environments.
- Run as a Docker Compose service; credentials originate from the user's existing gcx config + OS keyring via a host-side export script.

## Non-goals (for now)

- Dashboards, alerts, traces (Tempo), profiles, SLOs, IRM, any Grafana Cloud product APIs.
- Any write operation of any kind.
- Authentication on the MCP HTTP endpoint (it is bound to localhost; see Security).
- Mapping human environment names ("stg") to environments — handled by the user's private skill.

## Anti-goals (would be a failure even if it "works")

- Any path by which a tool can issue a non-GET Grafana request (e.g. a generic `gcx api` passthrough, or letting a caller choose the gcx subcommand).
- A token appearing in a log line, an error message, a tool result, the image, or the git repo.
- Org-specific strings landing in the public repo.
- Dumping unbounded log output into Claude's context.

## Architecture

```
grafana_server_mcp/
├── src/grafana_mcp/
│   ├── __init__.py
│   ├── __main__.py        # main(): MCP_TRANSPORT=stdio|http → mcp.run(...)
│   ├── server.py          # create_server(): FastMCP + lifespan (Settings, Environments, GcxRunner)
│   ├── config.py          # Settings (pydantic-settings) + environment parsing from GRAFANA_* vars
│   ├── gcx_config.py      # renders an in-memory/tmpfs gcx config.yaml (keychain off) from Environments
│   ├── gcx.py             # GcxRunner: allowlisted argv builder, subprocess exec, timeout, output parsing, redaction
│   ├── exceptions.py
│   ├── logging_setup.py   # structlog + log_tool_call (as in bitbucket)
│   └── tools/
│       ├── __init__.py    # register_all_tools(mcp)
│       ├── environments.py
│       ├── datasources.py
│       ├── metrics.py
│       └── logs.py
├── scripts/
│   └── gcx_to_env.py      # host-side: gcx config + keyring → .env
├── tests/
├── Dockerfile
├── docker-compose.yml
├── Makefile               # check (ruff, mypy strict, pytest), env, up, down, logs
├── .env.example
├── mcp_config_http.json
├── pyproject.toml         # name grafana-server-mcp, script grafana-mcp
├── .github/workflows/docker-image.yml, .github/dependabot.yml
└── README.md
```

Dependencies as in bitbucket (`fastmcp`, `pydantic`, `pydantic-settings`, `structlog`; dev: `pytest`, `pytest-asyncio`, `ruff`, `mypy`). No `httpx` needed — all Grafana access goes through gcx. Python 3.12, ruff line-length 100, mypy strict.

## Credential flow

### 1. Host side: `scripts/gcx_to_env.py`

Run by the user on their workstation whenever tokens change (`make env`, which runs `uv run scripts/gcx_to_env.py`; the script declares its own deps — `keyring` — via PEP 723 inline metadata so it does not pollute the server package).

1. Runs `gcx config view --raw -o json`. gcx may print a JSON "hint" line (`{"class":"hint",...}`) before the config on stdout — skip such lines before parsing.
2. For each context (or only those passed via `--contexts a,b`), resolves context → stack → `grafana.server`, `grafana.org-id`, `grafana.token`. Skips contexts without a stack (e.g. an empty `default`). Non-token auth methods (basic auth, OAuth, cloud tokens) are out of scope: skip with a warning.
3. Token resolution:
   - If the token starts with `keychain:` → the reference format is `keychain:<service>:<username>` where service is `gcx` and username is the remainder, e.g. `keychain:gcx:v2:<a>:<b>` → `keyring.get_password("gcx", "v2:<a>:<b>")`. Split on the **first two** colons only.
   - Otherwise the token is plaintext in the config; use as-is.
   - Known from the user's current config (2026-11-17): every context uses `auth-method: token` with Grafana service-account tokens; one is plaintext `glsa_…`, the rest are `keychain:gcx:v2:…` references. So static tokens are the expected case; OAuth/cloud tokens are only skipped-with-warning edge cases.
   - Fail clearly (naming the context, never the token) if the keyring entry is missing or the keyring is locked.
   - The `v2` prefix may mean the stored value is wrapped/encoded rather than the bare token. **The builder must verify** what `get_password` returns (without printing it — e.g. check prefix `glsa_` and length) and unwrap if needed.
4. Writes `.env` (default path `./.env`, `--output` to override) with mode `0600`, refusing to overwrite without `--force`. Never prints token values; prints a summary of environments written.
5. Preserves any non-`GRAFANA_*` lines already in an existing `.env` (e.g. `MCP_*` overrides) when overwriting with `--force`. *(Builder's call how; simplest is regenerate only a delimited block.)*

### 2. `.env` format (generic)

```
GRAFANA_ENVIRONMENTS=dev,stg-west,prod
GRAFANA_DEV_URL=https://grafana.example.com
GRAFANA_DEV_TOKEN=glsa_...
GRAFANA_DEV_ORG_ID=1
GRAFANA_STG_WEST_URL=...
...
```

- `GRAFANA_ENVIRONMENTS` holds the original context names, comma-separated; this is what tools accept and `list_environments` returns.
- Variable prefix per environment = name uppercased, every non-alphanumeric char → `_`. Two names that normalise to the same prefix is a startup error.
- `ORG_ID` optional, default 1.

### 3. Container side

On startup (in the lifespan, before serving):

1. `config.py` parses `GRAFANA_ENVIRONMENTS` and the per-environment vars into a list of `Environment(name, url, token, org_id)`; missing URL/token for a listed environment is a startup error naming the variable.
2. `gcx_config.py` writes a gcx config (`version: 1`, one stack + context per environment, `auth-method: token`, keychain disabled) to a file on tmpfs (compose mounts `tmpfs: /run/gcx`, mode 0700, file 0600), and the runner sets `GCX_CONFIG` to that path for every gcx call. The keychain must be off so gcx never tries to reach a Secret Service inside the container — the gcx binary contains `keychain: off` / `credentials.keychain` strings and honours a `GCX_KEYCHAIN` env var; **the builder must verify the exact config key / env value** against gcx 1.7.1 (`gcx config check` against the generated file is a good test).
3. Set `GCX_TELEMETRY` to disabled (verify the accepted value) and `HOME` to a writable tmp dir so gcx does not try to write elsewhere.

## Tools

All tools are read-only and every one requires `env` (an environment name from `list_environments`); there is **no default environment**. Each tool maps to exactly one fixed gcx subcommand; the caller never chooses the subcommand. All calls use `--context <env>` and JSON output.

| Tool | gcx command | Notes |
|---|---|---|
| `list_environments()` | none (from settings) | Returns `[{name, url, org_id}]`. Never the token. |
| `list_datasources(env, type?, name?)` | `datasources list` | `type` restricted to `prometheus` \| `loki` (default: both). Returns uid, name, type, isDefault. |
| `metrics_list_names(env, datasource?, match?)` | `metrics list-names` | |
| `metrics_labels(env, datasource?, label?, metric?, match?)` | `metrics labels` | |
| `metrics_query(env, expr, datasource?, since?, from?, to?, step?, time?)` | `metrics query` | Instant query unless `since`/`from` given → range query. |
| `logs_labels(env, datasource?, label?, query?)` | `logs labels` | |
| `logs_query(env, expr, datasource?, since?, from?, to?, limit?)` | `logs query` | `limit` default 100, max 1000. Newest first. |
| `logs_metrics(env, expr, datasource?, since?, from?, to?, step?)` | `logs metrics` | Metric LogQL (e.g. `sum(count_over_time(...[5m]))`). |

**Datasource resolution (my call):** `datasource` accepts a UID or an exact name. If omitted and the environment has exactly one datasource of the needed type, use it; if it has several, return an error listing them (uid + name) so Claude can pick. Resolution results may be cached per environment for a few minutes.

**Time parameters:** passed straight through to gcx (`since` like `1h`/`24h`; `from`/`to` as RFC3339, Unix, or relative `now-1d`). Validate shape lightly; let gcx report errors.

**Server instructions** (FastMCP `instructions`) should tell Claude the workflow: `list_environments` → `list_datasources` → discover labels/metric names → query; and that environment names are exactly as listed (no hardcoded meaning).

**Output shaping:**

- Parse gcx JSON, drop hint lines, return compact structured results (e.g. for logs: timestamp, labels, line; for metrics: series labels + values).
- Hard cap on total characters per tool result (setting, default ~50k); when truncated, say so in the result and how to narrow (shorter range, more selective query, lower limit).
- gcx errors → a clear tool error containing gcx's error summary/details, with every configured token value scrubbed from the text as a defence in depth.

## Read-only enforcement

1. **Allowlist by construction:** `GcxRunner` exposes only methods for the subcommands above; argv is built from a fixed list, user input only ever lands in flag values / positional query argument, never as a subcommand or flag name. Executed via `asyncio.create_subprocess_exec` (no shell).
2. A test asserts every argv the runner can produce starts with an allowlisted subcommand pair and never contains `api`, `create`, `update`, `delete`, `push`, `login`, `config set`, etc.
3. Never pass user strings that start with `-` as positional arguments without `--` separation (or use `--expr` for queries).
4. README recommends (but does not require) Viewer-role service-account tokens so Grafana enforces read-only too.

## Runtime / Docker

- **Image:** multi-stage on `python:3.12-slim`, non-root `mcp` user, like bitbucket. gcx binary downloaded from its official GitHub release, version pinned via build arg (`GCX_VERSION=1.7.1`) with checksum verification against the release's checksum file; the builder must look up the real release repo/asset naming. Image contains no config and no secrets.
- **Compose:** single service, `env_file: .env`, port published on **localhost only**: `127.0.0.1:${GRAFANA_MCP_PORT:-8003}:8000` (default host port 8003 because the sibling bitbucket server already uses 8000; configurable), `tmpfs: /run/gcx`, log volume, `restart: unless-stopped`, healthcheck as in bitbucket.
- **Network access to Grafana:** the Grafana instances are only reachable through a corporate VPN (with an additional HTTP proxy) or Zscaler Private Access on the host. Verified 2026-11-17 on the user's machine: with ZPA connected, a plain rootless Podman container on the default network reached a Grafana `/api/health` with **strict TLS verification** (HTTP 200) — so DNS, routing and the server certificate all work from a container without extra setup. Therefore:
  - Default compose uses normal (bridge) networking, no `network_mode: host`.
  - Optional proxy pass-through for the VPN+proxy case: `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` read from `.env` (empty by default) and passed to the gcx subprocess (gcx is Go and honours them). Never written into logs.
  - Optional extra CA bundle for proxies that intercept TLS: if `GRAFANA_EXTRA_CA_FILE` is set, compose mounts it read-only and the entrypoint appends it to a copy of the system bundle used via `SSL_CERT_FILE`. Off by default. *(Builder's call on mechanism; must not require root at runtime.)*
  - README troubleshooting section: "works on host but not in container" → check VPN/proxy vars, CA, and as a last resort a commented `network_mode: host` override in which the server must bind `127.0.0.1` itself (`MCP_HTTP_HOST=127.0.0.1`), since port publishing does not apply.
  - The VPN+proxy path was **not** tested (only ZPA was connected); the builder should keep it simple and document it as best-effort.
- **Container runtime:** the user's `docker` is **Podman 4.9 (rootless)** with `/usr/bin/docker-compose` as the compose provider. Dockerfile and compose must work under Podman: fully qualified base image names (`docker.io/library/python:3.12-slim`), no Docker-only compose features, healthcheck via Python (not curl). Plain Docker should work too.
- **Transport:** `MCP_TRANSPORT=http` in the image; stdio still supported for local dev (`MCP_TRANSPORT=stdio`, using the same env vars).
- **Limits (settings):** per-call gcx timeout (default 60s), max concurrent gcx processes (default 4), max result chars.
- **Client registration:** `claude mcp add --transport http grafana http://127.0.0.1:8003/mcp`; `mcp_config_http.json` example.

## Logging

structlog like bitbucket: one line per tool call (tool, env, duration, outcome, result size, truncated?), never the token, never full log contents. Query expressions may be logged (they are the user's own queries); keep that at debug level.

## Security notes

- Tokens live in a 0600 `.env` on the host and in the container's environment / tmpfs config; visible to anyone who can `docker inspect` on the host. Accepted for a single-user workstation.
- No auth on the MCP endpoint; mitigated by binding to 127.0.0.1. Documented in README: do not publish the port beyond localhost.

## Testing

Offline unit tests with a fake gcx runner (subprocess mocked):

- env parsing: normalisation, collisions, missing vars, org-id default.
- gcx config rendering: structure, permissions, no keychain.
- argv building per tool, allowlist test, `--` / `--expr` handling of hostile input.
- output parsing incl. hint-line skipping, truncation, error mapping, token scrubbing.
- datasource resolution (uid, name, single, ambiguous).
- `gcx_to_env.py`: keychain ref parsing, plaintext token, missing entry, file mode 0600, no token in stdout, `--contexts`, `--force`.
- A smoke test (skipped unless `GCX_SMOKE=1`) that runs the real gcx binary against the generated config with `gcx config check`.

`make check` = ruff + mypy strict + pytest, as in siblings.

## Calls I made (user may override)

- Package/script names: `grafana_mcp`, `grafana-mcp`, project `grafana-server-mcp`.
- Datasource optional with auto-pick when unambiguous.
- Log limit default 100 / max 1000; result cap ~50k chars; gcx timeout 60s.
- Host port configurable, default 8003 (8000 is taken by bitbucket), bound to 127.0.0.1.
- Export script lives in this repo, run with `uv run`, keyring via PEP 723 deps.
- Bridge networking by default; proxy vars and extra CA are optional add-ons for the VPN+proxy case.

## Left to the builder

- Exact shape of compact tool results.
- Caching of datasource lookups.
- How `.env` regeneration preserves non-GRAFANA lines.
- Exact gcx flags per command (check `--help` for 1.7.1), exact keychain-off and telemetry-off mechanisms (verify).
