"""Prometheus tools: metric names, labels and PromQL queries."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context
from pydantic import Field

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


async def metrics_list_names(
    ctx: Context,
    env: str,
    datasource: str | None = None,
    match: list[str] | None = None,
    contains: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """List Prometheus metric names, optionally filtered.

    `datasource` is a UID or exact name (optional when the environment has one Prometheus).
    `contains` is a substring filter, e.g. "http_requests"; `match` is a list of series
    selectors, e.g. ['{job="api"}']. Returns {"names": [...], "total": N}.
    """
    async with log_tool_call("metrics_list_names", env):
        require_env(ctx, env)
        uid = await resolve_datasource(ctx, env, "prometheus", datasource)
        runner: GcxRunner = ctx.lifespan_context["runner"]
        data = await runner.run(
            env,
            "metrics list-names",
            flags_of(datasource=uid, match=match, contains=contains, limit=limit),
        )
        names = label_list(data)
        meta = data.get("list_meta") if isinstance(data, dict) else None
        total = meta.get("total") if isinstance(meta, dict) else None
        return result(ctx, {"names": names, "total": total if total is not None else len(names)})


async def metrics_labels(
    ctx: Context,
    env: str,
    datasource: str | None = None,
    label: str | None = None,
    metric: str | None = None,
    match: list[str] | None = None,
) -> dict[str, Any]:
    """List Prometheus label names, or the values of one `label`.

    Examples: label="job" lists job values; metric="http_requests_total" restricts to one
    metric; match=['{namespace="prod"}'] restricts by series selector.
    """
    async with log_tool_call("metrics_labels", env):
        require_env(ctx, env)
        uid = await resolve_datasource(ctx, env, "prometheus", datasource)
        runner: GcxRunner = ctx.lifespan_context["runner"]
        data = await runner.run(
            env,
            "metrics labels",
            flags_of(datasource=uid, label=label, metric=metric, match=match),
        )
        return result(ctx, label_list(data))


async def metrics_query(
    ctx: Context,
    env: str,
    expr: str,
    datasource: str | None = None,
    since: str | None = None,
    from_: Annotated[str | None, Field(alias="from")] = None,
    to: str | None = None,
    step: str | None = None,
    time: str | None = None,
) -> dict[str, Any]:
    """Run a PromQL query.

    With none of since/from/to this is an INSTANT query (optionally at `time`) returning
    [{"labels", "value"}]. With a range it returns [{"labels", "values": [[ts, v], ...]}].
    Time range: `since` (e.g. "1d") XOR `from` (e.g. "now-2h" or RFC3339) with optional `to`;
    `step` sets the resolution (e.g. "5m"). The MCP parameter is named `from`.

    Examples:
      expr='sum(increase(http_requests_total{code=~"5.."}[1d]))'
      expr='rate(http_requests_total[5m])', since="6h", step="5m"
    """
    async with log_tool_call("metrics_query", env):
        require_env(ctx, env)
        validate_time(since, from_, to, time)
        uid = await resolve_datasource(ctx, env, "prometheus", datasource)
        runner: GcxRunner = ctx.lifespan_context["runner"]
        data = await runner.run(
            env,
            "metrics query",
            flags_of(datasource=uid, expr=expr, since=since, to=to, step=step, time=time)
            | ({"from": from_} if from_ is not None else {}),
        )
        return result(ctx, compact_prom(data))
