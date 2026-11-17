"""Render a private gcx config file with the OS keychain and telemetry disabled.

Spike findings (gcx 1.7.1, verified with fake data, isolated HOME/GCX_CONFIG, and
DBUS_SESSION_BUS_ADDRESS unset):
- gcx accepts a JSON document as config.yaml (JSON is valid YAML); no PyYAML needed.
- Top-level `"credentials": {"keychain": "off"}` is recognised and validated (a bogus value
  fails with "invalid credentials.keychain value"); `gcx config view` echoes it back, and
  the plaintext token in the stack is used (auth method: token).
- Env `GCX_KEYCHAIN=off` also works. `false` and `0` are NOT accepted: gcx warns
  "not a recognized value and was ignored; the OS credential store is still in use".
- Telemetry is disabled with env `GCX_TELEMETRY=disabled` (or config
  `diagnostics: {telemetry: disabled}`).
- `gcx config check` exits non-zero when the server is unreachable, so tests assert on its
  output ("Configuration: valid") rather than the exit code.
We use both the config key and the env vars (belt and braces).
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from grafana_mcp.config import Environment

GCX_SUBPROCESS_FLAGS: dict[str, str] = {
    "GCX_KEYCHAIN": "off",
    "GCX_TELEMETRY": "disabled",
}


def render_gcx_config(envs: Sequence[Environment]) -> dict[str, Any]:
    return {
        "version": 1,
        "stacks": {
            e.name: {
                "grafana": {
                    "server": e.url,
                    "token": e.token.get_secret_value(),
                    "auth-method": "token",
                    "org-id": e.org_id,
                }
            }
            for e in envs
        },
        "contexts": {e.name: {"stack": e.name} for e in envs},
        "current-context": envs[0].name,
        "credentials": {"keychain": "off"},
    }


def write_gcx_config(envs: Sequence[Environment], directory: Path | None) -> Path:
    """Write config.yaml (mode 0600) into a 0700 directory and return its path."""
    if directory is None:
        directory = Path(tempfile.mkdtemp(prefix="gcx-"))
    else:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / "config.yaml"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(render_gcx_config(envs)))
    path.chmod(0o600)
    return path
