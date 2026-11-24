from __future__ import annotations

from typing import Any

import pytest

from grafana_mcp.exceptions import DatasourceResolutionError
from grafana_mcp.tools._common import resolve_datasource
from grafana_mcp.tools.datasources import list_datasources
from tests.conftest import FakeRunner


async def test_resolve_datasource_by_uid(ctx: Any) -> None:
    assert await resolve_datasource(ctx, "dev", "prometheus", "prom-uid-1") == "prom-uid-1"


async def test_resolve_datasource_by_name(ctx: Any) -> None:
    assert await resolve_datasource(ctx, "dev", "loki", "Loki") == "loki-uid-1"


async def test_resolve_datasource_single_default(ctx: Any) -> None:
    assert await resolve_datasource(ctx, "dev", "prometheus", None) == "prom-uid-1"


async def test_resolve_datasource_unknown_name(ctx: Any) -> None:
    with pytest.raises(DatasourceResolutionError, match=r"prom-uid-1 \(Prometheus\)"):
        await resolve_datasource(ctx, "dev", "prometheus", "nope")


async def test_resolve_datasource_ambiguous_lists_options(ctx: Any, runner: FakeRunner) -> None:
    runner.responses["datasources list"]["datasources"].append(
        {"uid": "prom-uid-2", "name": "Prometheus 2", "type": "prometheus", "default": False}
    )
    with pytest.raises(DatasourceResolutionError) as exc:
        await resolve_datasource(ctx, "dev", "prometheus", None)
    msg = str(exc.value)
    assert "prom-uid-1 (Prometheus)" in msg
    assert "prom-uid-2 (Prometheus 2)" in msg


async def test_resolve_datasource_cached(ctx: Any, runner: FakeRunner) -> None:
    await resolve_datasource(ctx, "dev", "prometheus", None)
    await resolve_datasource(ctx, "dev", "prometheus", "Prometheus")
    assert len(runner.calls) == 1


async def test_resolve_datasource_cache_expires(
    ctx: Any, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    await resolve_datasource(ctx, "dev", "prometheus", None)
    key = ("dev", "prometheus")
    stamp, items = ctx.lifespan_context["ds_cache"][key]
    ctx.lifespan_context["ds_cache"][key] = (stamp - 301, items)
    await resolve_datasource(ctx, "dev", "prometheus", None)
    assert len(runner.calls) == 2


async def test_list_datasources_filters_type(ctx: Any, runner: FakeRunner) -> None:
    result = await list_datasources(ctx, "dev", type="loki")
    assert runner.calls == [("dev", "datasources list", {"type": "loki"})]
    assert result["result"] == [
        {"uid": "loki-uid-1", "name": "Loki", "type": "loki", "isDefault": False}
    ]


async def test_list_datasources_all_types_single_call(ctx: Any, runner: FakeRunner) -> None:
    runner.responses["datasources list"]["datasources"].append(
        {"uid": "cw-uid", "name": "cloudwatch", "type": "cloudwatch", "default": False}
    )
    result = await list_datasources(ctx, "dev")
    assert runner.calls == [("dev", "datasources list", {})]
    assert [d["type"] for d in result["result"]] == ["prometheus", "loki", "cloudwatch"]
    assert result["result"][0]["isDefault"] is True


async def test_list_datasources_name_filter(ctx: Any, runner: FakeRunner) -> None:
    await list_datasources(ctx, "dev", name="o")
    assert runner.calls == [("dev", "datasources list", {"name": "o"})]
