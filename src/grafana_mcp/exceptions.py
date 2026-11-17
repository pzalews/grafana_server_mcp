"""Exception hierarchy for the Grafana MCP server."""

from __future__ import annotations


class GrafanaMcpError(Exception):
    """Base class for all server errors."""


class ConfigError(GrafanaMcpError):
    """Invalid or incomplete configuration."""


class UnknownEnvironmentError(GrafanaMcpError):
    """Requested environment is not configured."""


class DatasourceResolutionError(GrafanaMcpError):
    """Datasource could not be resolved."""


class GcxError(GrafanaMcpError):
    """gcx exited with an error."""


class GcxTimeoutError(GcxError):
    """gcx did not finish within the timeout."""


class InvalidArgumentError(GrafanaMcpError):
    """A tool argument is invalid."""
