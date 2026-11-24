from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP

from .datasources import list_datasources
from .environments import list_environments
from .folders import list_folders
from .logs import logs_labels, logs_metrics, logs_query
from .metrics import metrics_labels, metrics_list_names, metrics_query


def register_all_tools(mcp: FastMCP) -> None:
    tools: list[Callable[..., Any]] = [
        list_environments,
        list_datasources,
        list_folders,
        metrics_list_names,
        metrics_labels,
        metrics_query,
        logs_labels,
        logs_query,
        logs_metrics,
    ]
    for fn in tools:
        mcp.add_tool(fn)
