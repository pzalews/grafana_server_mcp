from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from grafana_mcp.config import Environment

FIXTURES = Path(__file__).parent / "fixtures"
DEV_TOKEN = "glsa_dev_secret_token_value"
PROD_TOKEN = "glsa_prod_secret_token_value"


class FakeRunner:
    """Records (env, command, flags) and returns canned data per command."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.responses: dict[str, Any] = responses or {}

    async def run(self, env: str, command: str, flags: dict[str, Any]) -> Any:
        self.calls.append((env, command, dict(flags)))
        data = self.responses.get(command, {})
        if command == "datasources list" and isinstance(data, dict) and "datasources" in data:
            wanted = flags.get("type")
            return {
                "datasources": [
                    d for d in data["datasources"] if wanted is None or d["type"] == wanted
                ]
            }
        return data


@pytest.fixture
def datasources_payload() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / "datasources.json").read_text())
    return payload


@pytest.fixture
def runner(datasources_payload: dict[str, Any]) -> FakeRunner:
    return FakeRunner({"datasources list": datasources_payload})


@pytest.fixture
def ctx(runner: FakeRunner) -> Any:
    envs = {
        "dev": Environment(
            name="dev", url="https://grafana.example.com", token=SecretStr(DEV_TOKEN)
        ),
        "prod": Environment(
            name="prod", url="https://grafana.example.com", token=SecretStr(PROD_TOKEN), org_id=2
        ),
    }
    settings = SimpleNamespace(grafana_max_result_chars=50_000)
    return SimpleNamespace(
        lifespan_context={"settings": settings, "envs": envs, "runner": runner, "ds_cache": {}}
    )
