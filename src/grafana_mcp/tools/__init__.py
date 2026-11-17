from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP

from .datasources import list_datasources
from .environments import list_environments


def register_all_tools(mcp: FastMCP) -> None:
    tools: list[Callable[..., Any]] = [
        list_environments,
        list_datasources,
    ]
    for fn in tools:
        mcp.add_tool(fn)
