"""Grafana folder listing (`gcx resources get folders`)."""

from __future__ import annotations

from typing import Any

from fastmcp import Context

from grafana_mcp.gcx import GcxRunner
from grafana_mcp.logging_setup import log_tool_call

from ._common import require_env, result

_PARENT_ANNOTATION = "grafana.app/folder"


def compact_folders(data: Any) -> Any:
    """Reduce Folder resources to {uid, title, parentUid, url}; pass unknown shapes through."""
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return data
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            return data
        meta = item.get("metadata") or {}
        spec = item.get("spec") or {}
        annotations = meta.get("annotations") or {}
        out.append(
            {
                "uid": meta.get("name"),
                "title": spec.get("title"),
                "parentUid": annotations.get(_PARENT_ANNOTATION),
                "url": item.get("url"),
            }
        )
    out.sort(key=lambda f: str(f["title"] or "").lower())
    return out


async def list_folders(ctx: Context, env: str) -> dict[str, Any]:
    """List the Grafana folders of an environment: [{"uid", "title", "parentUid", "url"}].

    `uid` is the folder UID used in dashboard URLs (/dashboards/f/<uid>); `parentUid` is null
    for top-level folders. Sorted by title.
    """
    async with log_tool_call("list_folders", env):
        require_env(ctx, env)
        runner: GcxRunner = ctx.lifespan_context["runner"]
        data = await runner.run(env, "resources get folders", {})
        return result(ctx, compact_folders(data))
