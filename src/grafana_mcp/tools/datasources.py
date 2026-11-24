from __future__ import annotations

from typing import Any

from fastmcp import Context

from grafana_mcp.gcx import GcxRunner
from grafana_mcp.logging_setup import log_tool_call

from ._common import extract_datasources, flags_of, require_env, result


async def list_datasources(
    ctx: Context,
    env: str,
    type: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """List all datasources of an environment (uid, name, type, isDefault).

    `type` restricts to one datasource type, e.g. "prometheus", "loki" or "cloudwatch"
    (default: all types); `name` is a case-insensitive substring filter.
    Only Prometheus and Loki datasources can be queried by the other tools.
    """
    async with log_tool_call("list_datasources", env):
        require_env(ctx, env)
        runner: GcxRunner = ctx.lifespan_context["runner"]
        data = await runner.run(
            env, "datasources list", flags_of(type=type or None, name=name or None)
        )
        items = [
            {
                "uid": d.get("uid"),
                "name": d.get("name"),
                "type": d.get("type"),
                "isDefault": bool(d.get("default", False)),
            }
            for d in extract_datasources(data)
        ]
        return result(ctx, items)
