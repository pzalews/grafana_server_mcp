import os
from pathlib import Path

import pytest

from grafana_mcp.config import Settings, env_prefix, load_environments
from grafana_mcp.exceptions import ConfigError

BASE = {
    "GRAFANA_ENVIRONMENTS": "dev, prod",
    "GRAFANA_DEV_URL": "https://grafana.example.com/",
    "GRAFANA_DEV_TOKEN": "t1",
    "GRAFANA_PROD_URL": "https://grafana.example.com",
    "GRAFANA_PROD_TOKEN": "t2",
    "GRAFANA_PROD_ORG_ID": "3",
}


def test_env_prefix() -> None:
    assert env_prefix("stg-west") == "STG_WEST"
    assert env_prefix("prod.eu 2") == "PROD_EU_2"


def test_load_environments_basic() -> None:
    envs = load_environments(BASE)
    assert [e.name for e in envs] == ["dev", "prod"]
    assert envs[0].url == "https://grafana.example.com"
    assert envs[0].org_id == 1
    assert envs[1].org_id == 3
    assert envs[0].token.get_secret_value() == "t1"


def test_missing_token_names_variable() -> None:
    src = {k: v for k, v in BASE.items() if k != "GRAFANA_DEV_TOKEN"}
    with pytest.raises(ConfigError, match="GRAFANA_DEV_TOKEN"):
        load_environments(src)


def test_prefix_collision() -> None:
    with pytest.raises(ConfigError) as ei:
        load_environments({"GRAFANA_ENVIRONMENTS": "stg-west,stg_west"})
    assert "stg-west" in str(ei.value) and "stg_west" in str(ei.value)


def test_empty_environments() -> None:
    with pytest.raises(ConfigError):
        load_environments({"GRAFANA_ENVIRONMENTS": ""})


def test_bad_url() -> None:
    src = {**BASE, "GRAFANA_ENVIRONMENTS": "dev", "GRAFANA_DEV_URL": "ftp://x"}
    with pytest.raises(ConfigError):
        load_environments(src)


def test_redacted_has_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in BASE.items():
        monkeypatch.setenv(k, v)
    settings = Settings()  # type: ignore[call-arg]
    assert "t1" not in str(settings.redacted())
    assert "dev" in settings.redacted()["environments"]


@pytest.mark.parametrize(
    "var,value",
    [
        ("GRAFANA_MAX_CONCURRENCY", "0"),
        ("GRAFANA_MAX_RESULT_CHARS", "999"),
        ("GRAFANA_GCX_TIMEOUT", "0"),
    ],
)
def test_settings_reject_out_of_range(
    monkeypatch: pytest.MonkeyPatch, var: str, value: str
) -> None:
    monkeypatch.setenv("GRAFANA_ENVIRONMENTS", "dev")
    monkeypatch.setenv(var, value)
    with pytest.raises(ValueError):
        Settings()  # type: ignore[call-arg]


def test_settings_read_grafana_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for k in [k for k in os.environ if k.startswith("GRAFANA_")]:
        monkeypatch.delenv(k)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "grafana.env").write_text(
        "GRAFANA_ENVIRONMENTS=dev\n"
        "GRAFANA_DEV_URL=https://grafana.example.com\n"
        "GRAFANA_DEV_TOKEN=glsa_x\n"
    )
    envs = Settings().environments()  # type: ignore[call-arg]
    assert [e.name for e in envs] == ["dev"]
