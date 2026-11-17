import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from pydantic import SecretStr

from grafana_mcp.config import Environment
from grafana_mcp.gcx_config import GCX_SUBPROCESS_FLAGS, render_gcx_config, write_gcx_config

ENVS = [
    Environment(name="dev", url="https://grafana.example.com", token=SecretStr("glsa_fake")),
    Environment(
        name="prod", url="https://grafana.example.com", token=SecretStr("glsa_fake2"), org_id=3
    ),
]


def test_render_structure() -> None:
    cfg = render_gcx_config(ENVS)
    assert cfg["version"] == 1
    assert cfg["contexts"]["prod"] == {"stack": "prod"}
    assert cfg["current-context"] == "dev"
    assert cfg["stacks"]["dev"]["grafana"]["auth-method"] == "token"
    assert cfg["stacks"]["dev"]["grafana"]["token"] == "glsa_fake"
    assert cfg["stacks"]["prod"]["grafana"]["org-id"] == 3
    assert cfg["credentials"] == {"keychain": "off"}


def test_subprocess_flags() -> None:
    assert GCX_SUBPROCESS_FLAGS == {"GCX_KEYCHAIN": "off", "GCX_TELEMETRY": "disabled"}


def test_write_permissions(tmp_path: Path) -> None:
    d = tmp_path / "cfgdir"
    path = write_gcx_config(ENVS, d)
    assert path == d / "config.yaml"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    assert json.loads(path.read_text()) == render_gcx_config(ENVS)


def test_write_default_tempdir() -> None:
    path = write_gcx_config(ENVS, None)
    try:
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        shutil.rmtree(path.parent)


@pytest.mark.skipif(shutil.which("gcx") is None, reason="gcx not installed")
def test_written_file_parses_with_gcx(tmp_path: Path) -> None:
    path = write_gcx_config(ENVS, tmp_path / "c")
    env = {
        **os.environ,
        **GCX_SUBPROCESS_FLAGS,
        "GCX_CONFIG": str(path),
        "HOME": str(tmp_path),
        "DBUS_SESSION_BUS_ADDRESS": "",
    }
    gcx = shutil.which("gcx")
    assert gcx
    proc = subprocess.run(
        [gcx, "config", "check"], env=env, capture_output=True, text=True, timeout=60
    )
    out = (proc.stdout + proc.stderr).lower()
    assert "configuration: valid" in out
    assert "invalid credentials.keychain" not in out
    assert "not a recognized value" not in out
    assert "keyring" not in out
