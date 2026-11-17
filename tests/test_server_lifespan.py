from __future__ import annotations

from pathlib import Path

import pytest

from grafana_mcp import gcx_config, server
from grafana_mcp.server import _lifespan, mcp


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAFANA_ENVIRONMENTS", "dev")
    monkeypatch.setenv("GRAFANA_DEV_URL", "https://grafana.example.com")
    monkeypatch.setenv("GRAFANA_DEV_TOKEN", "tok-secret")
    monkeypatch.delenv("GRAFANA_GCX_CONFIG_DIR", raising=False)
    monkeypatch.chdir(Path(__file__).parent)  # no stray .env


async def test_auto_created_dir_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _lifespan(mcp) as ctx:
        cfg = Path(ctx["runner"]._env["GCX_CONFIG"])
        assert cfg.exists()
    assert not cfg.parent.exists()


async def test_user_dir_keeps_other_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    user = tmp_path / "mine"
    user.mkdir()
    (user / "keep.txt").write_text("x")
    monkeypatch.setenv("GRAFANA_GCX_CONFIG_DIR", str(user))
    async with _lifespan(mcp) as ctx:
        cfg = Path(ctx["runner"]._env["GCX_CONFIG"])
        assert cfg.exists()
    assert (user / "keep.txt").exists()
    assert not cfg.exists()


async def test_runner_failure_removes_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[Path] = []
    real = gcx_config.write_gcx_config

    def spy(envs, directory):  # type: ignore[no-untyped-def]
        path = real(envs, directory)
        seen.append(path)
        return path

    def boom(**_: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(server, "write_gcx_config", spy)
    monkeypatch.setattr(server, "GcxRunner", boom)
    with pytest.raises(RuntimeError):
        async with _lifespan(mcp):
            pass
    assert seen and not seen[0].exists()
