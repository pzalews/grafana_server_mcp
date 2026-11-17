from __future__ import annotations

import json
from typing import Any

import pytest

from grafana_mcp.exceptions import InvalidArgumentError
from grafana_mcp.tools._common import validate_time
from grafana_mcp.tools.metrics import metrics_labels, metrics_list_names, metrics_query
from tests.conftest import FIXTURES, FakeRunner


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


async def test_metrics_query_instant_flags(ctx: Any, runner: FakeRunner) -> None:
    runner.responses["metrics query"] = fixture("metrics_query_instant")
    await metrics_query(ctx, "dev", "up")
    env, command, flags = runner.calls[-1]
    assert (env, command) == ("dev", "metrics query")
    assert flags["expr"] == "up"
    assert flags["datasource"] == "prom-uid-1"
    assert not {"since", "from", "to", "step"} & flags.keys()


async def test_metrics_query_instant_time(ctx: Any, runner: FakeRunner) -> None:
    runner.responses["metrics query"] = fixture("metrics_query_instant")
    await metrics_query(ctx, "dev", "up", time="2024-01-01T00:00:00Z")
    assert runner.calls[-1][2]["time"] == "2024-01-01T00:00:00Z"


async def test_metrics_query_range_flags(ctx: Any, runner: FakeRunner) -> None:
    runner.responses["metrics query"] = fixture("metrics_query_range")
    await metrics_query(ctx, "dev", "up", since="1d", step="5m")
    flags = runner.calls[-1][2]
    assert flags["since"] == "1d"
    assert flags["step"] == "5m"


async def test_metrics_query_from_to_flags(ctx: Any, runner: FakeRunner) -> None:
    runner.responses["metrics query"] = fixture("metrics_query_range")
    await metrics_query(ctx, "dev", "up", from_="now-2h", to="now")
    flags = runner.calls[-1][2]
    assert flags["from"] == "now-2h"
    assert flags["to"] == "now"


async def test_since_and_from_conflict(ctx: Any, runner: FakeRunner) -> None:
    with pytest.raises(InvalidArgumentError):
        await metrics_query(ctx, "dev", "up", since="1h", from_="now-2h")
    assert not [c for c in runner.calls if c[1] == "metrics query"]


def test_validate_time_rules() -> None:
    validate_time("1h", None, None)
    validate_time(None, "a", "b")
    with pytest.raises(InvalidArgumentError):
        validate_time("1h", "a", None)
    for kw in ({"since": "1h"}, {"from_": "a"}, {"to": "b"}):
        with pytest.raises(InvalidArgumentError):
            validate_time(**{"since": None, "from_": None, "to": None, **kw}, time="t")


async def test_metrics_query_compacts_instant(ctx: Any, runner: FakeRunner) -> None:
    runner.responses["metrics query"] = fixture("metrics_query_instant")
    out = await metrics_query(ctx, "dev", "up")
    assert out["truncated"] is False
    assert out["result"][0] == {
        "labels": {"__name__": "up", "job": "api", "instance": "10.0.0.1:8080"},
        "value": "1",
    }
    assert len(out["result"]) == 2


async def test_metrics_query_compacts_range(ctx: Any, runner: FakeRunner) -> None:
    runner.responses["metrics query"] = fixture("metrics_query_range")
    out = await metrics_query(ctx, "dev", "up", since="1h")
    assert out["result"][0]["labels"]["job"] == "api"
    assert out["result"][0]["values"] == [[1700000000, "1"], [1700000060, "1"], [1700000120, "0"]]


async def test_labels_tools_pass_selectors(ctx: Any, runner: FakeRunner) -> None:
    runner.responses["metrics labels"] = fixture("labels")
    out = await metrics_labels(ctx, "dev", match=['{job="api"}'])
    assert runner.calls[-1][2]["match"] == ['{job="api"}']
    assert out["result"] == ["app", "job", "namespace"]


async def test_list_names_flags_and_shape(ctx: Any, runner: FakeRunner) -> None:
    runner.responses["metrics list-names"] = {
        "data": ["up", "http_requests_total"],
        "list_meta": {"truncated": True, "returned": 2, "total": 900},
    }
    out = await metrics_list_names(ctx, "dev", contains="http", limit=2)
    flags = runner.calls[-1][2]
    assert flags["contains"] == "http" and flags["limit"] == 2
    assert out["result"] == {"names": ["up", "http_requests_total"], "total": 900}
