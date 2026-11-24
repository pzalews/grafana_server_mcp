from __future__ import annotations

from grafana_mcp.server import create_server


async def test_create_server_registers_tools() -> None:
    tools = await create_server().list_tools()
    assert {
        "list_environments",
        "list_datasources",
        "list_folders",
        "metrics_list_names",
        "metrics_labels",
        "metrics_query",
        "logs_labels",
        "logs_query",
        "logs_metrics",
    } <= {t.name for t in tools}
