# Grafana gcx MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only FastMCP server that answers metrics (Prometheus) and logs (Loki) questions across several Grafana environments by running a fixed allowlist of `gcx` commands, packaged as a localhost-only Docker/Podman Compose service, plus a host script that exports gcx config + keyring tokens into `.env`.

**Architecture:** `config.py` turns `GRAFANA_*` env vars into `Environment` objects; `gcx_config.py` renders them into a private gcx config file (keychain off); `gcx.py` (`GcxRunner`) is the only place a subprocess is spawned and only accepts allowlisted subcommands; `tools/*` are thin async functions over the runner. `scripts/gcx_to_env.py` is standalone (PEP 723) and runs on the host.

**Tech Stack:** Python 3.12, fastmcp ≥3.2.4, pydantic 2, pydantic-settings 2, structlog, pytest + pytest-asyncio, ruff, mypy strict, hatchling; gcx 1.7.1 binary; `keyring` (script only); Podman/Docker + docker-compose.

**Spec:** `docs/superpowers/specs/2026-11-17-grafana-gcx-mcp-design.md` — read it before starting any task.

## Global Constraints

- **Public repo:** no organisation names, stack/context names, Grafana URLs, datasource UIDs or label values in any committed file (code, tests, fixtures, README, commit messages). Use `dev`, `stg`, `stg-west`, `prod`, `https://grafana.example.com`. Fixtures captured from a real gcx run MUST be anonymised before commit. Before each commit, read `git diff --cached` for real hostnames, context names or UIDs.
- **Never print, log, return or commit a token.** When verifying real tokens, print only length and first 4 chars.
- Read-only: no code path may run a gcx subcommand outside the allowlist in Task 3.
- Mirror `../bitbucket_server_mcp` conventions: `src/` layout, `Settings` via pydantic-settings, `log_tool_call`, `register_all_tools(mcp)`, `__main__.main()` with `MCP_TRANSPORT=stdio|http`, ruff line-length 100, `mypy --strict`, `asyncio_mode = "auto"`.
- Names: package `grafana_mcp`, console script `grafana-mcp`, project `grafana-server-mcp`, image `grafana-server-mcp`.
- Env var scheme: `GRAFANA_ENVIRONMENTS=dev,stg-west,prod`; per env `GRAFANA_<PREFIX>_URL`, `GRAFANA_<PREFIX>_TOKEN`, `GRAFANA_<PREFIX>_ORG_ID` (optional, default 1); prefix = name uppercased, every non-alphanumeric char → `_`.
- Defaults: log `limit` 100 (max 1000), result cap 50 000 chars, gcx timeout 60 s, max 4 concurrent gcx processes, host port `127.0.0.1:${GRAFANA_MCP_PORT:-8003}` → container 8000.
- Every tool requires `env`; no default environment.
- The user's `docker` is rootless Podman 4.9 with `/usr/bin/docker-compose`: fully qualified image names (`docker.io/library/python:3.12-slim`), Python healthcheck.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Inherited `GRAFANA_TOKEN` / `GRAFANA_SERVER` / `GRAFANA_<X>_TOKEN` in the server's environment leaking into the gcx subprocess, which gcx honours and which would silently override `--context` → gcx must get a minimal, explicit env. Test `test_subprocess_env_is_minimal` (Task 3).
2. Real-world query text: LogQL with quotes, pipes, newlines, or an expression starting with `-` must reach gcx as a single untouched argument and never be read as a flag. Test `test_hostile_expr_is_single_flag_value` (Task 3).
3. gcx failing: non-zero exit with a JSON error on stdout, hint lines before JSON, plain-text stderr, timeout → a clear tool error carrying gcx's summary/details with every token scrubbed. Tests `test_error_json_surfaces_summary`, `test_error_text_is_scrubbed`, `test_timeout_kills_process` (Task 3).
4. Env name typos / case ("STG" vs "stg") → error listing valid names, no gcx call. Test `test_unknown_env_lists_valid_names` (Task 4).
5. Huge results (busy log streams, high-cardinality label lists) → trimmed, still valid structured output, with `truncated: true` and a narrowing hint. Test `test_fit_result_trims_list` (Task 3) and `test_logs_query_truncates` (Task 5).

---

### Task 1: Project scaffold and environment config

**Files:**
- Create: `pyproject.toml` (copy bitbucket's; rename; deps = fastmcp, pydantic, pydantic-settings, structlog — no httpx/tenacity), `.gitignore` (copy bitbucket's), `src/grafana_mcp/__init__.py`, `src/grafana_mcp/config.py`, `src/grafana_mcp/exceptions.py`, `src/grafana_mcp/logging_setup.py` (copy bitbucket's; `log_tool_call(tool_name: str, env: str | None = None)` binds `tool`, `env`)
- Test: `tests/__init__.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `env_prefix(name: str) -> str`
  - `class Environment(BaseModel)`: `name: str`, `url: str` (trailing `/` stripped, must start with http/https), `token: SecretStr`, `org_id: int = 1`
  - `load_environments(source: Mapping[str, str]) -> list[Environment]` — raises `ConfigError`
  - `class Settings(BaseSettings)` (env_file `.env`, extra ignore): `grafana_environments: str`, `grafana_gcx_path: str = "gcx"`, `grafana_gcx_timeout: float = 60`, `grafana_max_concurrency: int = 4`, `grafana_max_result_chars: int = 50_000`, `grafana_gcx_config_dir: str | None = None`, `grafana_log_path: str | None = None`, `mcp_transport: Literal["stdio","http"] = "stdio"`, `mcp_http_host: str = "127.0.0.1"`, `mcp_http_port: int = 8000`; method `environments() -> list[Environment]` (merges `dotenv_values(".env")` under `os.environ`, os.environ wins, then `load_environments`); `redacted() -> dict[str, Any]` (env names + urls, no tokens)
  - `exceptions.py`: `GrafanaMcpError(Exception)`; subclasses `ConfigError`, `UnknownEnvironmentError`, `DatasourceResolutionError`, `GcxError`, `GcxTimeoutError(GcxError)`, `InvalidArgumentError`

- [ ] **Step 1: Write failing tests** in `tests/test_config.py`:
  - `test_env_prefix`: `env_prefix("stg-west") == "STG_WEST"`, `env_prefix("prod.eu 2") == "PROD_EU_2"`
  - `test_load_environments_basic`: source `{"GRAFANA_ENVIRONMENTS": "dev, prod", "GRAFANA_DEV_URL": "https://grafana.example.com/", "GRAFANA_DEV_TOKEN": "t1", "GRAFANA_PROD_URL": "https://grafana.example.com", "GRAFANA_PROD_TOKEN": "t2", "GRAFANA_PROD_ORG_ID": "3"}` → names `["dev","prod"]`, dev url without trailing slash, dev `org_id == 1`, prod `org_id == 3`, `token.get_secret_value() == "t1"`
  - `test_missing_token_names_variable`: missing `GRAFANA_DEV_TOKEN` → `ConfigError` whose message contains `"GRAFANA_DEV_TOKEN"`
  - `test_prefix_collision`: `"stg-west,stg_west"` → `ConfigError` containing both names
  - `test_empty_environments`: `""` → `ConfigError`
  - `test_bad_url`: `ftp://x` → `ConfigError`
  - `test_redacted_has_no_token`: `"t1" not in str(settings.redacted())`

- [ ] **Step 2: Run** `uv pip install -e ".[dev]" && python -m pytest tests/test_config.py -v` — Expected: FAIL (import errors).

- [ ] **Step 3: Implement** `config.py`, `exceptions.py`, `logging_setup.py`, `__init__.py`. `load_environments` collects all problems before raising (one message listing every missing var).

- [ ] **Step 4: Run** `python -m pytest tests/test_config.py -v && ruff check src tests && mypy src` — Expected: all pass.

- [ ] **Step 5: Commit** `feat: project scaffold and environment config`.

---

### Task 2: gcx config rendering (keychain off) — verified against real gcx

**Files:**
- Create: `src/grafana_mcp/gcx_config.py`
- Test: `tests/test_gcx_config.py`

**Interfaces:**
- Consumes: `Environment` (Task 1)
- Produces:
  - `render_gcx_config(envs: Sequence[Environment]) -> dict[str, Any]` — `{"version": 1, "stacks": {name: {"grafana": {"server": url, "token": <plain>, "auth-method": "token", "org-id": org_id}}}, "contexts": {name: {"stack": name}}, "current-context": envs[0].name, <keychain-off key>}`
  - `write_gcx_config(envs: Sequence[Environment], directory: Path | None) -> Path` — if `directory` is None use `tempfile.mkdtemp(prefix="gcx-")`; dir mode 0700, file `config.yaml` mode 0600 (create with `os.open(..., 0o600)`), content is `json.dumps(...)` (JSON is valid YAML — no PyYAML dependency)
  - `GCX_SUBPROCESS_FLAGS: dict[str, str]` — env vars that disable keychain and telemetry for gcx (values verified in Step 1)

- [ ] **Step 1: Verify gcx mechanics (spike, nothing committed).** In the scratchpad, write a config with two fake environments (`https://grafana.example.com`, token `glsa_fake`) and run `GCX_CONFIG=<file> HOME=<tmp> gcx config check` and `gcx config view` under: (a) top-level `"credentials": {"keychain": "off"}`, (b) env `GCX_KEYCHAIN=off`/`false`/`0`. Also find the accepted `GCX_TELEMETRY` off value (`gcx --help`, `strings`). Pick the combination where gcx reports the plaintext token is used and does not touch the keyring (no D-Bus prompt; `DBUS_SESSION_BUS_ADDRESS=` unset should not cause an error). Record the result as a comment at the top of `gcx_config.py`.

- [ ] **Step 2: Write failing tests**:
  - `test_render_structure`: for envs dev/prod, `cfg["contexts"]["prod"] == {"stack": "prod"}`, `cfg["stacks"]["dev"]["grafana"]["auth-method"] == "token"`, keychain-off key present with verified value
  - `test_write_permissions`: file mode `0o600`, dir mode `0o700`, `json.loads(path.read_text())` round-trips
  - `test_written_file_parses_with_gcx` marked `@pytest.mark.skipif(shutil.which("gcx") is None)`: `gcx config check` with `GCX_CONFIG` set exits 0 (network errors in its output are acceptable; assert exit code and that output does not contain "keychain" errors — adapt to what Step 1 showed)

- [ ] **Step 3: Run** `python -m pytest tests/test_gcx_config.py -v` — Expected: FAIL.

- [ ] **Step 4: Implement** `gcx_config.py`.

- [ ] **Step 5: Run** tests + ruff + mypy — Expected: PASS.

- [ ] **Step 6: Commit** `feat: render private gcx config with keychain disabled`.

---

### Task 3: GcxRunner — allowlist, subprocess, parsing, scrubbing, truncation

**Files:**
- Create: `src/grafana_mcp/gcx.py`
- Test: `tests/test_gcx_runner.py`, `tests/fakes.py` (a fake gcx: a small Python script written to `tmp_path` and made executable, whose behaviour is driven by env var `FAKE_GCX_MODE` = `ok|hint|error_json|stderr|sleep|echo_argv|echo_env`)

**Interfaces:**
- Consumes: `GCX_SUBPROCESS_FLAGS` (Task 2), exceptions (Task 1)
- Produces:
  - `Command = Literal["datasources list", "metrics list-names", "metrics labels", "metrics query", "logs labels", "logs query", "logs metrics"]`; `ALLOWED_COMMANDS: frozenset[str]`
  - `build_argv(gcx_path: str, env: str, command: Command, flags: Mapping[str, str | int | list[str] | None]) -> list[str]` → `[gcx_path, *command.split(), f"--context={env}", "-o", "json", *flag_args]`; every flag rendered as `--name=value` (lists repeat the flag, None omitted). Raises `InvalidArgumentError` if `command not in ALLOWED_COMMANDS` or a flag name is not in a per-command allowed-flag table (e.g. `metrics query`: `datasource, expr, since, from, to, step, time`; `logs query`: `datasource, expr, since, from, to, limit`; `logs metrics`: `datasource, expr, since, from, to, step`; `metrics labels`: `datasource, label, metric, match`; `metrics list-names`: `datasource, match, contains, limit`; `logs labels`: `datasource, label, query`; `datasources list`: `type, name`). Positional arguments are never used; queries go via `--expr=`.
  - `parse_output(stdout: str) -> Any` — drop lines that are JSON objects with `"class": "hint"`; parse the rest as one JSON document; fall back to JSON-lines list; raise `GcxError` if nothing parses
  - `scrub(text: str, secrets: Iterable[str]) -> str` — replaces each non-empty secret with `***`
  - `fit_result(data: Any, max_chars: int) -> dict[str, Any]` → `{"result": data, "truncated": False}` or, when `len(json.dumps(data)) > max_chars`: lists trimmed from the end (for dicts, trim the largest list value) until it fits, `{"result": trimmed, "truncated": True, "note": "Result truncated to fit N chars; narrow the time range, add label filters, or lower limit."}`; strings cut as last resort
  - `class GcxRunner`: `__init__(self, gcx_path: str, config_path: Path, timeout: float, max_concurrency: int, secrets: Sequence[str], passthrough_env: Mapping[str, str])`; `async run(self, env: str, command: Command, flags: Mapping[...]) -> Any`. Subprocess env = exactly `PATH`, `HOME` (a private tmp dir), `GCX_CONFIG`, `GCX_SUBPROCESS_FLAGS`, plus `HTTPS_PROXY/HTTP_PROXY/NO_PROXY` (and lowercase) and `SSL_CERT_FILE` taken from `passthrough_env` when non-empty. Uses `asyncio.create_subprocess_exec`, `asyncio.Semaphore(max_concurrency)`, `asyncio.wait_for(..., timeout)`; on timeout kill + `GcxTimeoutError`. Non-zero exit → `GcxError` whose message is gcx's `error.summary` + `error.details` if stdout/stderr holds a `gcx.error` JSON, else stderr tail (≤2000 chars); always passed through `scrub`.

- [ ] **Step 1: Write failing tests:**
  - `test_build_argv_metrics_query`: `build_argv("gcx","prod","metrics query",{"datasource":"abc","expr":"up","since":"1h","from":None})` == `["gcx","metrics","query","--context=prod","-o","json","--datasource=abc","--expr=up","--since=1h"]`
  - `test_build_argv_rejects_unknown_command` (`"api"`, `"dashboards delete"`) and `test_build_argv_rejects_unknown_flag` (`{"jq": "."}`) → `InvalidArgumentError`
  - `test_allowlist_is_read_only`: no entry in `ALLOWED_COMMANDS` contains any of `api create update delete push pull login set unset edit use-context adaptive`
  - `test_hostile_expr_is_single_flag_value`: expr `'-{app="x"} |= "a b"\n| json'` → appears exactly once as `'--expr=-{app="x"} |= "a b"\n| json'`
  - `test_parse_output_skips_hint`: `'{"class":"hint","summary":"x"}\n{"data":1}'` → `{"data": 1}`
  - `test_scrub`: `scrub("bad token glsa_abc", ["glsa_abc", ""]) == "bad token ***"`
  - `test_fit_result_trims_list`: 1000-item list, `max_chars=2000` → `truncated` True, `len(json.dumps(out)) <= 2300`, items are a prefix of the original
  - `test_run_ok` (fake mode `ok`) returns parsed JSON
  - `test_error_json_surfaces_summary` (mode `error_json`, exit 1, stdout `{"type":"gcx.error","error":{"summary":"Datasource not found","details":"uid xyz"}}`) → `GcxError` message contains both strings
  - `test_error_text_is_scrubbed` (mode `stderr` echoing the secret) → message lacks the secret, contains `***`
  - `test_timeout_kills_process` (mode `sleep`, timeout 0.5) → `GcxTimeoutError` within 3 s
  - `test_subprocess_env_is_minimal` (mode `echo_env`, with `os.environ` monkeypatched to include `GRAFANA_TOKEN`, `GRAFANA_SERVER`, `GRAFANA_DEV_TOKEN`) → none of those keys in the child env; `GCX_CONFIG` present; `HTTPS_PROXY` present only when passed
  - `test_concurrency_limit` (mode `sleep` 0.3s, 8 calls, max 2) → total time ≥ 1.1 s

- [ ] **Step 2: Run** `python -m pytest tests/test_gcx_runner.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement** `gcx.py` and `tests/fakes.py`.

- [ ] **Step 4: Run** tests + ruff + mypy — Expected: PASS.

- [ ] **Step 5: Commit** `feat: allowlisted gcx runner with scrubbing and truncation`.

---

### Task 4: Server, environments and datasources tools

**Files:**
- Create: `src/grafana_mcp/server.py`, `src/grafana_mcp/__main__.py` (as bitbucket), `src/grafana_mcp/tools/__init__.py`, `src/grafana_mcp/tools/_common.py`, `src/grafana_mcp/tools/environments.py`, `src/grafana_mcp/tools/datasources.py`
- Test: `tests/conftest.py` (fixture `ctx` = object with `lifespan_context` dict holding `settings`, `envs`, and a `FakeRunner` recording `(env, command, flags)` and returning canned data), `tests/test_tools_environments.py`, `tests/test_tools_datasources.py`

**Interfaces:**
- Consumes: Tasks 1–3
- Produces:
  - lifespan context keys: `"settings": Settings`, `"envs": dict[str, Environment]`, `"runner": GcxRunner`, `"ds_cache": dict[tuple[str,str], tuple[float, list[dict[str, Any]]]]`
  - `_common.require_env(ctx, env: str) -> Environment` — raises `UnknownEnvironmentError("Unknown environment 'STG'. Available: dev, prod")` (exact-match; no case folding)
  - `_common.resolve_datasource(ctx, env: str, ds_type: Literal["prometheus","loki"], datasource: str | None) -> str` — returns UID; accepts UID or exact name; None + exactly one of type → that UID; None + several/zero → `DatasourceResolutionError` listing `uid (name)` pairs. Uses `datasources list --type=<ds_type>` with a 300 s per-(env,type) cache in `ds_cache`.
  - `_common.result(ctx, data: Any) -> dict[str, Any]` = `fit_result(data, settings.grafana_max_result_chars)`
  - Tools: `list_environments(ctx) -> list[dict]` (`name, url, org_id`); `list_datasources(ctx, env: str, type: Literal["prometheus","loki"] | None = None, name: str | None = None) -> dict` (when type None, query both and concatenate; return `uid, name, type, isDefault` per item — adapt field names to real gcx JSON)
  - `server.create_server() -> FastMCP` with `instructions`: "Read-only access to Grafana metrics (Prometheus) and logs (Loki) via gcx. Always pass `env` exactly as returned by list_environments. Workflow: list_environments → list_datasources → metrics_list_names / metrics_labels / logs_labels to discover names and labels → metrics_query / logs_query / logs_metrics. Prefer narrow time ranges and label selectors." Lifespan: Settings → environments → `write_gcx_config` → `GcxRunner(secrets=[all tokens], passthrough_env=os.environ subset)` → log `startup` with `redacted()`; on shutdown remove the config dir.

- [ ] **Step 1: Capture real JSON shapes (not committed raw).** On the host, run `gcx datasources list --context=<a real ctx> -o json` once; note the field names; write an **anonymised** fixture `tests/fixtures/datasources.json` (uids like `prom-uid-1`, names like `Prometheus`, `Loki`).

- [ ] **Step 2: Write failing tests:**
  - `test_list_environments_no_tokens`: result names `["dev","prod"]`, no value equal to a token anywhere in `json.dumps(result)`
  - `test_unknown_env_lists_valid_names`: `list_datasources(ctx, "STG")` raises `UnknownEnvironmentError` with `"dev, prod"` in message; FakeRunner recorded no calls
  - `test_resolve_datasource_by_uid`, `test_resolve_datasource_by_name`, `test_resolve_datasource_single_default`, `test_resolve_datasource_ambiguous_lists_options`, `test_resolve_datasource_cached` (second call → no new runner call)
  - `test_list_datasources_filters_type`: runner called with `{"type": "loki"}`
  - `test_create_server_registers_tools` (in `tests/test_server_registration.py`): tool names ⊇ `{"list_environments","list_datasources"}` — extended in Task 5

- [ ] **Step 3: Run** — Expected: FAIL.

- [ ] **Step 4: Implement.**

- [ ] **Step 5: Run** all tests + ruff + mypy — Expected: PASS.

- [ ] **Step 6: Commit** `feat: server, environments and datasources tools`.

---

### Task 5: Metrics and logs tools

**Files:**
- Create: `src/grafana_mcp/tools/metrics.py`, `src/grafana_mcp/tools/logs.py`, anonymised fixtures `tests/fixtures/{metrics_query_instant,metrics_query_range,logs_query,logs_metrics,labels}.json`
- Modify: `src/grafana_mcp/tools/__init__.py`, `tests/test_server_registration.py`
- Test: `tests/test_tools_metrics.py`, `tests/test_tools_logs.py`

**Interfaces:**
- Consumes: `_common.require_env`, `resolve_datasource`, `result`, `log_tool_call(tool, env=)`
- Produces (all `async`, first param `ctx: Context`, return `dict[str, Any]` from `result(...)`):
  - `metrics_list_names(env, datasource=None, match: list[str] | None = None, contains: str | None = None, limit: int = 200)`
  - `metrics_labels(env, datasource=None, label=None, metric=None, match: list[str] | None = None)`
  - `metrics_query(env, expr, datasource=None, since=None, from_=None, to=None, step=None, time=None)` — tool param exposed to MCP as `from` (use `Annotated[str | None, Field(alias="from")]` or name it `start`/`end` if FastMCP aliasing is awkward — pick one, document in docstring)
  - `logs_labels(env, datasource=None, label=None, query=None)`
  - `logs_query(env, expr, datasource=None, since="1h", from_=None, to=None, limit=100)`
  - `logs_metrics(env, expr, datasource=None, since="1h", from_=None, to=None, step=None)`
  - `_common.validate_time(since, from_, to, time=None) -> None` — `InvalidArgumentError` if `since` and `from_` both set, or `time` combined with any of since/from/to
- Rules: `logs_query.limit` must be 1..1000 else `InvalidArgumentError`; `metrics_query` with none of since/from/to → instant query (pass `time` if given); logs results compacted to `[{"ts", "labels", "line"}]` newest first (adapt to real gcx JSON from fixtures); metrics results compacted to `[{"labels", "value"}]` (instant) or `[{"labels", "values": [[ts, v], ...]}]` (range). Docstrings carry concrete examples using generic labels (e.g. `sum(increase(http_requests_total{code=~"5.."}[1d]))`, `{app="ingress-gateway"} |= "error"`).

- [ ] **Step 1: Capture real shapes** for each command against a real context (instant, range, logs, logs metrics, labels), anonymise (generic labels, `example.com`, fake UIDs, invented log lines), save fixtures.

- [ ] **Step 2: Write failing tests:**
  - `test_metrics_query_instant_flags`: no time args → runner flags have `expr`, `datasource`, no `since/from/to`
  - `test_metrics_query_range_flags`: `since="1d", step="5m"` → flags include both
  - `test_since_and_from_conflict` → `InvalidArgumentError`, no runner call
  - `test_metrics_query_compacts_instant` / `_range` using fixtures
  - `test_logs_query_default_limit_100` and `test_logs_query_limit_bounds` (0 and 1001 rejected)
  - `test_logs_query_compacts_newest_first`
  - `test_logs_query_truncates`: fixture of 5000 lines + `grafana_max_result_chars=5000` → `truncated is True`, `note` present
  - `test_labels_tools_pass_selectors`: `metrics_labels(match=['{job="api"}'])` → flag `match` list; `logs_labels(query='{app="x"}')` → flag `query`
  - registration test now expects all 8 tools

- [ ] **Step 3: Run** — Expected: FAIL.

- [ ] **Step 4: Implement.**

- [ ] **Step 5: Run** full suite + ruff + mypy — Expected: PASS.

- [ ] **Step 6: Commit** `feat: metrics and logs query tools`.

---

### Task 6: Host export script `scripts/gcx_to_env.py`

**Files:**
- Create: `scripts/gcx_to_env.py` (PEP 723 header: `requires-python = ">=3.12"`, `dependencies = ["keyring>=25"]`), `tests/test_gcx_to_env.py`
- Modify: `Makefile` (target `env: uv run scripts/gcx_to_env.py $(ARGS)`)

**Interfaces:**
- Produces (module-level functions, importable in tests via `importlib.util.spec_from_file_location`; `keyring` imported lazily inside `main`):
  - `env_prefix(name: str) -> str` (same rule as Task 1; test asserts equality with `grafana_mcp.config.env_prefix` on several inputs)
  - `parse_keychain_ref(ref: str) -> tuple[str, str]` — `"keychain:gcx:v2:A:B"` → `("gcx", "v2:A:B")`; splits on the first two colons; `ValueError` otherwise
  - `load_gcx_config(gcx: str = "gcx") -> dict[str, Any]` — runs `gcx config view --raw -o json`, drops hint lines (same rule as `parse_output`)
  - `collect(config: dict[str, Any], only: set[str] | None, get_password: Callable[[str, str], str | None]) -> tuple[list[ExportedEnv], list[str]]` — `ExportedEnv(name, url, token, org_id)` dataclass; warnings for contexts without stack, non-token auth-method, unknown names in `only`; raises `SystemExit` with a message naming the context (never the token) if a keychain entry is missing
  - `unwrap_token(value: str) -> str` — identity unless Step 1 shows the keyring value is wrapped
  - `render_block(envs: list[ExportedEnv]) -> str` — between markers `# >>> grafana-mcp (generated by scripts/gcx_to_env.py) >>>` and `# <<< grafana-mcp <<<`
  - `merge_env(existing: str | None, block: str) -> str` — replace an existing marked block, or append; keep all other lines
  - `main(argv: list[str] | None = None) -> int` — flags `--contexts a,b`, `--output PATH` (default `.env`), `--force` (required when output exists); writes with mode 0600 via `os.open`; prints `Wrote N environments to PATH: a, b` and warnings; never prints tokens

- [ ] **Step 1: Verify keyring value shape (spike).** On the host: `uv run --no-project --with keyring python3 -c '...'` resolving one `keychain:` ref and printing only `len(v)`, `v[:4]`. If it is not `glsa`-prefixed, inspect structure (e.g. base64/JSON) printing only key names/lengths, and implement `unwrap_token` accordingly with a test using a synthetic wrapped value.

- [ ] **Step 2: Write failing tests:**
  - `test_parse_keychain_ref`, `test_parse_keychain_ref_rejects_plain`
  - `test_collect_plain_and_keychain`: config with `dev` (plain `glsa_dev`) and `prod` (`keychain:gcx:v2:a:b`), fake `get_password` returning `glsa_prod` for `("gcx","v2:a:b")` → both exported with right tokens
  - `test_collect_skips_default_context` and `test_collect_skips_non_token_auth` (warning text names the context)
  - `test_collect_missing_keyring_entry` → `SystemExit`, message contains context name, not the ref secret part
  - `test_collect_only_filter`
  - `test_merge_env_replaces_block_keeps_other_lines`: existing `"MCP_HTTP_PORT=9000\n<old block>\nFOO=1\n"` → both `MCP_HTTP_PORT=9000` and `FOO=1` kept, old block gone
  - `test_main_writes_0600_and_no_token_in_stdout` (monkeypatch `load_gcx_config` and keyring) — mode `0o600`, `capsys` output lacks tokens
  - `test_main_refuses_existing_without_force`
  - `test_prefix_matches_server`

- [ ] **Step 3: Run** — Expected: FAIL.

- [ ] **Step 4: Implement.**

- [ ] **Step 5: Run** full suite + ruff + mypy (include `scripts/` in ruff) — Expected: PASS.

- [ ] **Step 6: Real run** on the host: `make env ARGS="--output /tmp/<scratch>/test.env"`; verify `stat -c %a` is `600`, `grep -c _TOKEN=` equals env count, and print token lengths/prefixes only. Delete the file.

- [ ] **Step 7: Commit** `feat: export gcx config and keyring tokens to .env`.

---

### Task 7: Container, compose, CI and docs

**Files:**
- Create: `Dockerfile`, `docker-entrypoint.sh`, `docker-compose.yml`, `.dockerignore`, `.env.example`, `mcp_config_http.json`, `.github/workflows/docker-image.yml`, `.github/dependabot.yml`, `README.md`
- Modify: `Makefile` (bitbucket targets renamed + `env`, `up`, `down`, `logs`; `run-http` uses port 8000)

**Interfaces:**
- Consumes: console script `grafana-mcp`; env scheme from Global Constraints; `GRAFANA_EXTRA_CA_FILE`, proxy vars

- [ ] **Step 1: Dockerfile.** Multi-stage like bitbucket on `docker.io/library/python:3.12-slim`. Extra stage downloads gcx: `ARG GCX_VERSION=1.7.1`, `ARG TARGETARCH`; find the official release repo and asset/checksum naming (e.g. via `gh release view v1.7.1 -R <repo>`), download archive + checksums, verify with `sha256sum -c --ignore-missing`, install to `/usr/local/bin/gcx`. Runtime: non-root `mcp` user, `/var/log/grafana-mcp` owned by mcp, `ENV MCP_TRANSPORT=http MCP_HTTP_HOST=0.0.0.0 MCP_HTTP_PORT=8000`, `EXPOSE 8000`, `ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]`.

- [ ] **Step 2: Entrypoint** (`sh`, `set -eu`): if `/run/extra-ca.pem` is non-empty (`-s`), concatenate `/etc/ssl/certs/ca-certificates.crt` + it into `/tmp/ca-bundle.pem` and `export SSL_CERT_FILE=/tmp/ca-bundle.pem`; `exec grafana-mcp`.

- [ ] **Step 3: Compose:** service `grafana-mcp`, `build: .`, `env_file: .env`, `ports: ["127.0.0.1:${GRAFANA_MCP_PORT:-8003}:8000"]`, `tmpfs: [/tmp]`, `volumes: ["${GRAFANA_EXTRA_CA_FILE:-/dev/null}:/run/extra-ca.pem:ro", "mcp_logs:/var/log/grafana-mcp"]`, `environment: [GRAFANA_LOG_PATH=/var/log/grafana-mcp/server.log]`, `restart: unless-stopped`, Python urllib healthcheck on `http://localhost:8000/mcp/` (as bitbucket). Commented-out `network_mode: host` block with `MCP_HTTP_HOST=127.0.0.1` and a note.

- [ ] **Step 4: `.env.example`** with placeholder dev/prod on `https://grafana.example.com`, `glsa_xxx` tokens, commented `HTTPS_PROXY`, `NO_PROXY`, `GRAFANA_EXTRA_CA_FILE`, `GRAFANA_MCP_PORT=8003`. `.dockerignore` excludes `.env`, `.git`, `tests`, `docs`, caches. `mcp_config_http.json` → `http://localhost:8003/mcp/`.

- [ ] **Step 5: CI** — copy bitbucket workflow, image `grafana-server-mcp`, and add a preceding `test` job (`uv pip install --system -e ".[dev]"`, `make check`). Dependabot: pip, github-actions, docker.

- [ ] **Step 6: README** (generic only): what it does, tool list, quickstart (`make env` → `docker-compose up -d --build` → `claude mcp add --transport http grafana http://127.0.0.1:8003/mcp`), env scheme, security (localhost binding, `.env` 0600, recommend Viewer service-account tokens, tokens visible to `docker inspect`), mapping friendly env names via your own Claude skill, troubleshooting (works on host not in container → proxy vars / extra CA / host networking), Podman notes.

- [ ] **Step 7: Verify build & run:** `docker build -t grafana-server-mcp .` succeeds; `docker run --rm --entrypoint gcx grafana-server-mcp version` prints `1.7.1`; `make env` (real) then `docker-compose up -d --build`; `docker-compose ps` shows healthy; `ss -ltn | grep 8003` shows `127.0.0.1:8003` only; with Zscaler connected, call via a scratch fastmcp client against `http://127.0.0.1:8003/mcp`: `list_environments`, `list_datasources`, a `metrics_query` with `since="1h"`, a `logs_query` limit 5 → all non-error. `docker-compose logs` contains no `glsa_`. Then `docker-compose down`.

- [ ] **Step 8: Public-repo hygiene check:** `git grep -nIiE '<each real context name>|<real domain fragment>'` over the whole tree returns nothing (run with the real strings typed in the shell only, never written to a file). `make check` passes.

- [ ] **Step 9: Commit** `feat: container, compose, CI and README`.
