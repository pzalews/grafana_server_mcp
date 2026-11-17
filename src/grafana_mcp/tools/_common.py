"""Shared helpers for tools: environment validation, datasource resolution, result shaping."""

from __future__ import annotations

import time
from typing import Any, Literal

from fastmcp import Context

from grafana_mcp.config import Environment
from grafana_mcp.exceptions import DatasourceResolutionError, UnknownEnvironmentError
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
