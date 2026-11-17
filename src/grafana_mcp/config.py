"""Configuration for the Grafana MCP server.

Environments are declared via GRAFANA_ENVIRONMENTS=dev,stg-west,prod with per-environment
GRAFANA_<PREFIX>_URL, GRAFANA_<PREFIX>_TOKEN and optional GRAFANA_<PREFIX>_ORG_ID.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import BaseModel, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from grafana_mcp.exceptions import ConfigError


def env_prefix(name: str) -> str:
    """Uppercase the name and replace every non-alphanumeric char with underscore."""
    return re.sub(r"[^A-Za-z0-9]", "_", name).upper()


class Environment(BaseModel):
    name: str
    url: str
    token: SecretStr
    org_id: int = 1

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"url must start with http:// or https://, got: {v!r}")
        return v


def load_environments(source: Mapping[str, str]) -> list[Environment]:
    """Build environments from a flat variable mapping, reporting every problem at once."""
    names = [n.strip() for n in source.get("GRAFANA_ENVIRONMENTS", "").split(",") if n.strip()]
    if not names:
        raise ConfigError("GRAFANA_ENVIRONMENTS is empty; list environment names, e.g. dev,prod")

    problems: list[str] = []
    seen: dict[str, str] = {}
    for name in names:
        prefix = env_prefix(name)
        if prefix in seen and seen[prefix] != name:
            problems.append(
                f"environments {seen[prefix]!r} and {name!r} collide on prefix GRAFANA_{prefix}_"
            )
        seen.setdefault(prefix, name)
    if problems:
        raise ConfigError("; ".join(problems))

    envs: list[Environment] = []
    for name in dict.fromkeys(names):
        prefix = env_prefix(name)
        url = source.get(f"GRAFANA_{prefix}_URL", "").strip()
        token = source.get(f"GRAFANA_{prefix}_TOKEN", "").strip()
        org = source.get(f"GRAFANA_{prefix}_ORG_ID", "").strip()
        missing = [
            var
            for var, val in ((f"GRAFANA_{prefix}_URL", url), (f"GRAFANA_{prefix}_TOKEN", token))
            if not val
        ]
        if missing:
            problems.append(f"environment {name!r}: missing {', '.join(missing)}")
            continue
        try:
            envs.append(
                Environment(
                    name=name,
                    url=url,
                    token=SecretStr(token),
                    org_id=int(org) if org else 1,
                )
            )
        except ValueError as exc:
            problems.append(f"environment {name!r}: invalid configuration ({_brief(exc)})")
    if problems:
        raise ConfigError("; ".join(problems))
    return envs


def _brief(exc: ValueError) -> str:
    # Never echo input values (could contain secrets); report the message only.
    errors = getattr(exc, "errors", None)
    if callable(errors):
        return "; ".join(str(e.get("msg", "invalid")) for e in errors())
    return "invalid value"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    grafana_environments: str
    grafana_gcx_path: str = "gcx"
    grafana_gcx_timeout: float = 60
    grafana_max_concurrency: int = 4
    grafana_max_result_chars: int = 50_000
    grafana_gcx_config_dir: str | None = None
    grafana_log_path: str | None = None

    mcp_transport: Literal["stdio", "http"] = "stdio"
    mcp_http_host: str = "127.0.0.1"
    mcp_http_port: int = 8000

    def environments(self) -> list[Environment]:
        merged: dict[str, str] = {k: v for k, v in dotenv_values(".env").items() if v is not None}
        merged.update(os.environ)
        merged["GRAFANA_ENVIRONMENTS"] = self.grafana_environments
        return load_environments(merged)

    def redacted(self) -> dict[str, Any]:
        return {
            "environments": {e.name: e.url for e in self.environments()},
            "gcx_path": self.grafana_gcx_path,
            "gcx_timeout": self.grafana_gcx_timeout,
            "max_concurrency": self.grafana_max_concurrency,
            "max_result_chars": self.grafana_max_result_chars,
            "transport": self.mcp_transport,
        }
