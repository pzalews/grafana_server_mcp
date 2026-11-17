from __future__ import annotations

from typing import Any

from fastmcp import Context

from grafana_mcp.gcx import GcxRunner
from grafana_mcp.logging_setup import log_tool_call

from ._common import DsType, extract_datasources, require_env, result


async def list_datasources(
    ctx: Context,
    env: str,
    type: DsType | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """List Prometheus and Loki datasources of an environment (uid, name, type, isDefault).

    `type` restricts to prometheus or loki (default both); `name` is a case-insensitive
    substring filter.
    """
    async with log_tool_call("list_datasources", env):
        require_env(ctx, env)
        runner: GcxRunner = ctx.lifespan_context["runner"]
        types: list[DsType] = [type] if type else ["prometheus", "loki"]
        items: list[dict[str, Any]] = []
        for ds_type in types:
            flags: dict[str, str] = {"type": ds_type}
            if name:
                flags["name"] = name
            data = await runner.run(env, "datasources list", flags)
            items.extend(
                {
                    "uid": d.get("uid"),
                    "name": d.get("name"),
                    "type": d.get("type"),
                    "isDefault": bool(d.get("default", False)),
                }
                for d in extract_datasources(data)
            )
        return result(ctx, items)
