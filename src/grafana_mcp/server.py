from __future__ import annotations

import os
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from .config import Settings
from .gcx import GcxRunner
from .gcx_config import write_gcx_config
from .logging_setup import configure_logging, get_logger
from .tools import register_all_tools

log = get_logger(__name__)


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    settings = Settings()  # type: ignore[call-arg]
    configure_logging(
        log_path=settings.grafana_log_path,
        json_logs=bool(settings.grafana_log_path),
    )
    envs = settings.environments()
    config_dir = Path(settings.grafana_gcx_config_dir) if settings.grafana_gcx_config_dir else None
    config_path = write_gcx_config(envs, config_dir)
    runner: GcxRunner | None = None
    try:
        runner = GcxRunner(
            gcx_path=settings.grafana_gcx_path,
            config_path=config_path,
            timeout=settings.grafana_gcx_timeout,
            max_concurrency=settings.grafana_max_concurrency,
            secrets=[e.token.get_secret_value() for e in envs],
            passthrough_env=os.environ,
        )
        log.info("grafana_mcp.startup", **settings.redacted())
        yield {
            "settings": settings,
            "envs": {e.name: e for e in envs},
            "runner": runner,
            "ds_cache": {},
        }
    finally:
        if runner is not None:
            runner.close()
        if config_dir is None:  # directory created by us (mkdtemp): remove it entirely
            shutil.rmtree(config_path.parent, ignore_errors=True)
        else:  # user-supplied directory: remove only our file
            config_path.unlink(missing_ok=True)
        log.info("grafana_mcp.shutdown")


def create_server() -> FastMCP:
    mcp = FastMCP(
        name="Grafana MCP",
        instructions=(
            "Read-only access to Grafana metrics (Prometheus) and logs (Loki) via gcx, plus "
            "datasource and folder listings. "
            "Always pass `env` exactly as returned by list_environments. "
            "Workflow: list_environments → list_datasources → metrics_list_names / "
            "metrics_labels / logs_labels to discover names and labels → metrics_query / "
            "logs_query / logs_metrics. Prefer narrow time ranges and label selectors."
        ),
        lifespan=_lifespan,
    )
    register_all_tools(mcp)
    return mcp


mcp = create_server()
