from __future__ import annotations

from typing import Any

from fastmcp import Context

from grafana_mcp.logging_setup import log_tool_call


async def list_environments(ctx: Context) -> list[dict[str, Any]]:
    """List the configured Grafana environments (name, url, org_id). Never includes tokens."""
    async with log_tool_call("list_environments"):
        envs = ctx.lifespan_context["envs"]
        return [{"name": e.name, "url": e.url, "org_id": e.org_id} for e in envs.values()]
