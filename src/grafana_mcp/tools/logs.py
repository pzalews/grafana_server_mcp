"""Loki tools: label discovery, log queries and metric (LogQL) queries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastmcp import Context
from pydantic import Field

from grafana_mcp.exceptions import InvalidArgumentError
from grafana_mcp.gcx import GcxRunner
from grafana_mcp.logging_setup import log_tool_call

from ._common import (
    compact_prom,
    flags_of,
    label_list,
    require_env,
    resolve_datasource,
    result,
    validate_time,
)

MAX_LOG_LIMIT = 1000


def _iso(ns: str) -> str:
    try:
        return datetime.fromtimestamp(int(ns) / 1e9, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return ns


def compact_logs(data: Any) -> Any:
    """Flatten Loki streams into [{"ts", "labels", "line"}], newest first."""
    payload = data.get("data") if isinstance(data, dict) else None
    streams = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(streams, list):
        return data
    entries: list[tuple[int, dict[str, Any]]] = []
    for stream in streams:
        labels = stream.get("stream", {}) if isinstance(stream, dict) else {}
        for v in stream.get("values", []) if isinstance(stream, dict) else []:
            if isinstance(v, dict):
                ts, line = str(v.get("timestamp", "")), v.get("line", "")
            elif isinstance(v, list) and len(v) >= 2:
                ts, line = str(v[0]), v[1]
            else:
                continue
            key = int(ts) if ts.isdigit() else 0
            entries.append((key, {"ts": _iso(ts), "labels": labels, "line": line}))
    entries.sort(key=lambda e: e[0], reverse=True)
    return [e for _, e in entries]


async def logs_labels(
    ctx: Context,
    env: str,
    datasource: str | None = None,
    label: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """List Loki label names, or the values of one `label`.

    `query` is an optional stream selector narrowing the search, e.g. '{app="ingress-gateway"}'.
    """
    async with log_tool_call("logs_labels", env):
        require_env(ctx, env)
        uid = await resolve_datasource(ctx, env, "loki", datasource)
        runner: GcxRunner = ctx.lifespan_context["runner"]
        data = await runner.run(
            env, "logs labels", flags_of(datasource=uid, label=label, query=query)
        )
        return result(ctx, label_list(data))


async def logs_query(
    ctx: Context,
    env: str,
    expr: str,
    datasource: str | None = None,
    since: str | None = "1h",
    from_: Annotated[str | None, Field(alias="from")] = None,
    to: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Run a LogQL log query; returns [{"ts", "labels", "line"}] newest first.

    Time range: `since` (default "1h") XOR `from` (e.g. "now-3h" or RFC3339) with optional
    `to`. `limit` is 1..1000 (default 100). The MCP parameter is named `from`.

    Examples:
      expr='{app="ingress-gateway"} |= "error"'
      expr='{namespace="prod"} | json | level="error"', since="30m", limit=50
    """
    async with log_tool_call("logs_query", env):
        require_env(ctx, env)
        if not 1 <= limit <= MAX_LOG_LIMIT:
            raise InvalidArgumentError(f"`limit` must be between 1 and {MAX_LOG_LIMIT}")
        if from_:
            since = None
        validate_time(since, from_, to)
        uid = await resolve_datasource(ctx, env, "loki", datasource)
        runner: GcxRunner = ctx.lifespan_context["runner"]
        flags = flags_of(datasource=uid, expr=expr, since=since, to=to, limit=limit)
        if from_ is not None:
            flags["from"] = from_
        data = await runner.run(env, "logs query", flags)
        return result(ctx, compact_logs(data))


async def logs_metrics(
    ctx: Context,
    env: str,
    expr: str,
    datasource: str | None = None,
    since: str | None = "1h",
    from_: Annotated[str | None, Field(alias="from")] = None,
    to: str | None = None,
    step: str | None = None,
) -> dict[str, Any]:
    """Run a LogQL metric query; returns [{"labels", "values": [[ts, v], ...]}].

    Time range as for logs_query (`since` default "1h" XOR `from`, optional `to`); `step` is
    the resolution (e.g. "5m"). The MCP parameter is named `from`.

    Example: expr='sum by (app) (count_over_time({namespace="prod"} |= "error" [5m]))'
    """
    async with log_tool_call("logs_metrics", env):
        require_env(ctx, env)
        if from_:
            since = None
        validate_time(since, from_, to)
        uid = await resolve_datasource(ctx, env, "loki", datasource)
        runner: GcxRunner = ctx.lifespan_context["runner"]
        flags = flags_of(datasource=uid, expr=expr, since=since, to=to, step=step)
        if from_ is not None:
            flags["from"] = from_
        data = await runner.run(env, "logs metrics", flags)
        return result(ctx, compact_prom(data))
