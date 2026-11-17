from __future__ import annotations

import json
from typing import Any

import pytest

from grafana_mcp.exceptions import InvalidArgumentError
from grafana_mcp.tools.logs import logs_labels, logs_metrics, logs_query
from tests.conftest import FIXTURES, FakeRunner


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


async def test_logs_query_default_limit_100(ctx: Any, runner: FakeRunner) -> None:
    runner.responses["logs query"] = fixture("logs_query")
    await logs_query(ctx, "dev", '{app="x"}')
    flags = runner.calls[-1][2]
    assert flags["limit"] == 100
    assert flags["since"] == "1h"
    assert flags["datasource"] == "loki-uid-1"
    assert flags["expr"] == '{app="x"}'


@pytest.mark.parametrize("limit", [0, 1001])
async def test_logs_query_limit_bounds(ctx: Any, runner: FakeRunner, limit: int) -> None:
    with pytest.raises(InvalidArgumentError):
        await logs_query(ctx, "dev", '{app="x"}', limit=limit)
    assert not [c for c in runner.calls if c[1] == "logs query"]


async def test_logs_query_from_replaces_since(ctx: Any, runner: FakeRunner) -> None:
    runner.responses["logs query"] = fixture("logs_query")
    await logs_query(ctx, "dev", '{app="x"}', from_="now-3h", to="now-1h")
    flags = runner.calls[-1][2]
    assert "since" not in flags
    assert flags["from"] == "now-3h" and flags["to"] == "now-1h"


async def test_logs_query_compacts_newest_first(ctx: Any, runner: FakeRunner) -> None:
    runner.responses["logs query"] = fixture("logs_query")
    out = await logs_query(ctx, "dev", '{app="x"}')
    lines = [e["line"] for e in out["result"]]
    assert lines == ["upstream timeout", "request handled", "upstream connect error"]
    first = out["result"][0]
    assert first["labels"] == {"app": "ingress-gateway", "level": "error"}
    assert first["ts"] == "2023-11-14T22:13:23Z"


async def test_logs_query_truncates(ctx: Any, runner: FakeRunner) -> None:
    ctx.lifespan_context["settings"].grafana_max_result_chars = 5000
    runner.responses["logs query"] = {
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {"app": "x"},
                    "values": [
                        {"timestamp": str(1700000000000000000 + i), "line": f"line number {i}"}
                        for i in range(5000)
                    ],
                }
            ],
        }
    }
    out = await logs_query(ctx, "dev", '{app="x"}', limit=1000)
    assert out["truncated"] is True
    assert "note" in out


async def test_logs_labels_pass_query(ctx: Any, runner: FakeRunner) -> None:
    runner.responses["logs labels"] = fixture("labels")
    out = await logs_labels(ctx, "dev", query='{app="x"}', label="level")
    flags = runner.calls[-1][2]
    assert flags["query"] == '{app="x"}' and flags["label"] == "level"
    assert out["result"] == ["app", "job", "namespace"]


async def test_logs_metrics_flags_and_shape(ctx: Any, runner: FakeRunner) -> None:
    runner.responses["logs metrics"] = fixture("logs_metrics")
    out = await logs_metrics(ctx, "dev", 'sum(count_over_time({app="api"}[1m]))', step="1m")
    flags = runner.calls[-1][2]
    assert flags["since"] == "1h" and flags["step"] == "1m"
    assert out["result"] == [
        {"labels": {"app": "api"}, "values": [[1700000000, "12"], [1700000060, "7"]]}
    ]
