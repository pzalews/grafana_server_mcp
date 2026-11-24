from __future__ import annotations

import json
from typing import Any

import pytest

from grafana_mcp.exceptions import UnknownEnvironmentError
from grafana_mcp.tools.folders import compact_folders, list_folders
from tests.conftest import FIXTURES, FakeRunner


async def test_list_folders(ctx: Any, runner: FakeRunner) -> None:
    runner.responses["resources get folders"] = json.loads((FIXTURES / "folders.json").read_text())
    result = await list_folders(ctx, "prod")
    assert runner.calls == [("prod", "resources get folders", {})]
    assert result["truncated"] is False
    assert result["result"] == [
        {
            "uid": "parent-uid",
            "title": "Infra",
            "parentUid": None,
            "url": "https://grafana.example.com/dashboards/f/parent-uid",
        },
        {
            "uid": "child-uid",
            "title": "nginx",
            "parentUid": "parent-uid",
            "url": "https://grafana.example.com/dashboards/f/child-uid",
        },
    ]


async def test_list_folders_unknown_env(ctx: Any) -> None:
    with pytest.raises(UnknownEnvironmentError):
        await list_folders(ctx, "nope")


def test_compact_folders_empty_and_unknown_shapes() -> None:
    assert compact_folders({"items": []}) == []
    assert compact_folders({"other": 1}) == {"other": 1}
