"""Shared helpers for tools: environment validation, datasource resolution, result shaping."""

from __future__ import annotations

import time
from typing import Any, Literal

from fastmcp import Context

from grafana_mcp.config import Environment
from grafana_mcp.exceptions import (
    DatasourceResolutionError,
    InvalidArgumentError,
    UnknownEnvironmentError,
)
from grafana_mcp.gcx import GcxRunner, fit_result

DsType = Literal["prometheus", "loki"]
DS_CACHE_TTL = 300.0


def require_env(ctx: Context, env: str) -> Environment:
    """Return the configured environment `env` (exact match) or raise with the valid names."""
    envs: dict[str, Environment] = ctx.lifespan_context["envs"]
    if env not in envs:
        raise UnknownEnvironmentError(f"Unknown environment {env!r}. Available: {', '.join(envs)}")
    return envs[env]


def result(ctx: Context, data: Any) -> dict[str, Any]:
    return fit_result(data, ctx.lifespan_context["settings"].grafana_max_result_chars)


def extract_datasources(data: Any) -> list[dict[str, Any]]:
    """Pull the datasource list out of gcx output (`{"datasources": [...]}`)."""
    items = data.get("datasources", []) if isinstance(data, dict) else data
    return [d for d in items if isinstance(d, dict)] if isinstance(items, list) else []


async def cached_datasources(ctx: Context, env: str, ds_type: DsType) -> list[dict[str, Any]]:
    """List datasources of one type for an environment, cached for 5 minutes."""
    require_env(ctx, env)
    cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = ctx.lifespan_context[
        "ds_cache"
    ]
    hit = cache.get((env, ds_type))
    if hit is not None and time.monotonic() - hit[0] < DS_CACHE_TTL:
        return hit[1]
    runner: GcxRunner = ctx.lifespan_context["runner"]
    data = await runner.run(env, "datasources list", {"type": ds_type})
    items = extract_datasources(data)
    cache[(env, ds_type)] = (time.monotonic(), items)
    return items


async def resolve_datasource(
    ctx: Context, env: str, ds_type: DsType, datasource: str | None
) -> str:
    """Resolve a UID or exact name (or the only datasource of the type) to a UID."""
    items = await cached_datasources(ctx, env, ds_type)
    options = ", ".join(f"{d.get('uid')} ({d.get('name')})" for d in items)
    if datasource:
        for d in items:
            if d.get("uid") == datasource:
                return str(d["uid"])
        for d in items:
            if d.get("name") == datasource:
                return str(d["uid"])
        raise DatasourceResolutionError(
            f"No {ds_type} datasource {datasource!r} in environment {env!r}. "
            f"Available: {options or 'none'}"
        )
    if len(items) == 1:
        return str(items[0]["uid"])
    if not items:
        raise DatasourceResolutionError(f"No {ds_type} datasources in environment {env!r}")
    raise DatasourceResolutionError(
        f"Several {ds_type} datasources in environment {env!r}; pass `datasource`. "
        f"Available: {options}"
    )


def validate_time(
    since: str | None, from_: str | None, to: str | None, time: str | None = None
) -> None:
    """Reject contradictory time arguments (since+from, or instant `time` with a range)."""
    if since and from_:
        raise InvalidArgumentError("`since` and `from` are mutually exclusive")
    if time and (since or from_ or to):
        raise InvalidArgumentError("`time` (instant query) cannot be combined with since/from/to")


def flags_of(**values: Any) -> dict[str, Any]:
    """Drop None values so only explicitly set flags reach gcx."""
    return {k: v for k, v in values.items() if v is not None}


def compact_prom(data: Any) -> Any:
    """Compact a Prometheus-style response to labels/value(s); pass unknown shapes through."""
    payload = data.get("data") if isinstance(data, dict) else None
    items = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return data
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            return data
        entry: dict[str, Any] = {"labels": item.get("metric", {})}
        if "values" in item:
            entry["values"] = item["values"]
        else:
            value = item.get("value")
            entry["value"] = value[1] if isinstance(value, list) and len(value) == 2 else value
        out.append(entry)
    return out


def label_list(data: Any) -> Any:
    """Extract the plain list from `{"status":..., "data": [...]}` label/name responses."""
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return data["data"]
    return data
