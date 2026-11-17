from __future__ import annotations

import json
from typing import Any

import pytest

from grafana_mcp.exceptions import UnknownEnvironmentError
from grafana_mcp.tools.datasources import list_datasources
from grafana_mcp.tools.environments import list_environments
from tests.conftest import DEV_TOKEN, PROD_TOKEN, FakeRunner


async def test_list_environments_no_tokens(ctx: Any) -> None:
    result = await list_environments(ctx)
    assert [e["name"] for e in result] == ["dev", "prod"]
    assert result[1]["org_id"] == 2
    dumped = json.dumps(result)
    assert DEV_TOKEN not in dumped
    assert PROD_TOKEN not in dumped


async def test_unknown_env_lists_valid_names(ctx: Any, runner: FakeRunner) -> None:
    with pytest.raises(UnknownEnvironmentError, match="dev, prod"):
        await list_datasources(ctx, "STG")
    assert runner.calls == []
