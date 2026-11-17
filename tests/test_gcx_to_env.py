import importlib.util
import os
import stat
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from grafana_mcp.config import env_prefix as server_env_prefix

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "gcx_to_env.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gcx_to_env", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gcx_to_env"] = mod
    spec.loader.exec_module(mod)
    return mod


m = _load()


def _cfg() -> dict[str, Any]:
    return {
        "version": 1,
        "stacks": {
            "dev": {
                "grafana": {
                    "server": "https://grafana.example.com/",
                    "token": "glsa_dev",
                    "auth-method": "token",
                    "org-id": 2,
                }
            },
            "prod": {
                "grafana": {
                    "server": "https://grafana.example.com",
                    "token": "keychain:gcx:v2:a:b",
                    "auth-method": "token",
                }
            },
            "basic": {"grafana": {"server": "https://g.example.com", "auth-method": "basic"}},
        },
        "contexts": {
            "dev": {"stack": "dev"},
            "prod": {"stack": "prod"},
            "basic": {"stack": "basic"},
            "orphan": {"stack": "gone"},
            "nostack": {},
            "default": {},
        },
        "current-context": "dev",
    }


def _get_password(service: str, user: str) -> str | None:
    return {("gcx", "v2:a:b"): "glsa_prod"}.get((service, user))


def test_parse_keychain_ref() -> None:
    assert m.parse_keychain_ref("keychain:gcx:v2:A:B") == ("gcx", "v2:A:B")


def test_parse_keychain_ref_rejects_plain() -> None:
    with pytest.raises(ValueError):
        m.parse_keychain_ref("glsa_abc")
    with pytest.raises(ValueError):
        m.parse_keychain_ref("keychain:gcx")


def test_collect_plain_and_keychain() -> None:
    cfg = _cfg()
    envs, _ = m.collect(cfg, {"dev", "prod"}, _get_password)
    by_name = {e.name: e for e in envs}
    assert by_name["dev"].token == "glsa_dev"
    assert by_name["dev"].url == "https://grafana.example.com"
    assert by_name["dev"].org_id == 2
    assert by_name["prod"].token == "glsa_prod"
    assert by_name["prod"].org_id == 1


def test_collect_empty_default_context_skipped_quietly() -> None:
    envs, warnings = m.collect(_cfg(), None, _get_password)
    assert "default" not in [e.name for e in envs]
    assert not any("'default'" in w for w in warnings)
    assert any("nostack" in w for w in warnings)
    assert any("orphan" in w for w in warnings)


def test_collect_exports_default_context_with_stack() -> None:
    cfg = _cfg()
    cfg["contexts"]["default"] = {"stack": "dev"}
    envs, _ = m.collect(cfg, None, _get_password)
    assert "default" in [e.name for e in envs]


def test_collect_skips_non_token_auth() -> None:
    envs, warnings = m.collect(_cfg(), None, _get_password)
    assert "basic" not in [e.name for e in envs]
    assert any("basic" in w for w in warnings)


def test_collect_missing_keyring_entry() -> None:
    with pytest.raises(SystemExit) as exc:
        m.collect(_cfg(), {"prod"}, lambda s, u: None)
    msg = str(exc.value)
    assert "prod" in msg
    assert "v2:a:b" not in msg


def test_collect_only_filter() -> None:
    envs, warnings = m.collect(_cfg(), {"dev", "nope"}, _get_password)
    assert [e.name for e in envs] == ["dev"]
    assert any("nope" in w for w in warnings)


def test_merge_env_replaces_block_keeps_other_lines() -> None:
    new = m.render_block([m.ExportedEnv("dev", "https://grafana.example.com", "glsa_new", 1)])
    old = m.render_block([m.ExportedEnv("old", "https://old.example.com", "glsa_old", 1)])
    merged = m.merge_env(f"MCP_HTTP_PORT=9000\n{old}\nFOO=1\n", new)
    assert "MCP_HTTP_PORT=9000" in merged
    assert "FOO=1" in merged
    assert "glsa_old" not in merged
    assert "GRAFANA_DEV_TOKEN=glsa_new" in merged
    assert merged.count(m.BEGIN) == 1


def test_merge_env_appends_when_no_block() -> None:
    block = m.render_block([m.ExportedEnv("dev", "https://grafana.example.com", "glsa_x", 1)])
    merged = m.merge_env("FOO=1", block)
    assert merged.startswith("FOO=1\n")
    assert m.BEGIN in merged
    assert m.merge_env(None, block).strip() == block.strip()


def test_render_block_vars() -> None:
    block = m.render_block([m.ExportedEnv("stg-west", "https://g.example.com", "glsa_x", 3)])
    assert "GRAFANA_ENVIRONMENTS=stg-west" in block
    assert "GRAFANA_STG_WEST_URL=https://g.example.com" in block
    assert "GRAFANA_STG_WEST_TOKEN=glsa_x" in block
    assert "GRAFANA_STG_WEST_ORG_ID=3" in block


def _patch_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "load_gcx_config", lambda gcx="gcx": _cfg())
    monkeypatch.setattr(m, "_keyring_get_password", _get_password)


def test_main_writes_0600_and_no_token_in_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_main(monkeypatch)
    out = tmp_path / "out.env"
    assert m.main(["--output", str(out), "--contexts", "dev,prod"]) == 0
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    text = out.read_text()
    assert "GRAFANA_PROD_TOKEN=glsa_prod" in text
    captured = capsys.readouterr()
    assert "glsa_" not in captured.out + captured.err
    assert "Wrote 2 environments" in captured.out


def test_main_refuses_existing_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_main(monkeypatch)
    out = tmp_path / "out.env"
    out.write_text("FOO=1\n")
    with pytest.raises(SystemExit):
        m.main(["--output", str(out), "--contexts", "dev"])
    assert out.read_text() == "FOO=1\n"
    assert m.main(["--output", str(out), "--contexts", "dev", "--force"]) == 0
    text = out.read_text()
    assert "FOO=1" in text
    assert "GRAFANA_DEV_TOKEN=glsa_dev" in text


@pytest.mark.parametrize("name", ["dev", "stg-west", "a.b c", "prod_1"])
def test_prefix_matches_server(name: str) -> None:
    assert m.env_prefix(name) == server_env_prefix(name)


def test_merge_env_unterminated_block_refuses() -> None:
    with pytest.raises(SystemExit):
        m.merge_env(f"A=1\n{m.BEGIN}\nGRAFANA_X=1\nB=2\n", "block\n")


def test_merge_env_duplicate_blocks_collapse_to_one() -> None:
    blk = m.render_block([m.ExportedEnv("dev", "https://g.example.com", "glsa_old", 1)])
    new = m.render_block([m.ExportedEnv("dev", "https://g.example.com", "glsa_new", 1)])
    merged = m.merge_env(f"A=1\n{blk}B=2\n{blk}C=3\n", new)
    assert merged.count(m.BEGIN) == 1
    assert "glsa_old" not in merged
    assert all(x in merged for x in ("A=1", "B=2", "C=3"))


def test_main_unterminated_block_leaves_file_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_main(monkeypatch)
    out = tmp_path / "out.env"
    original = f"A=1\n{m.BEGIN}\nB=2\n"
    out.write_text(original)
    with pytest.raises(SystemExit):
        m.main(["--output", str(out), "--contexts", "dev", "--force"])
    assert out.read_text() == original


def test_main_force_existing_0644_ends_0600_keeps_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_main(monkeypatch)
    out = tmp_path / "out.env"
    out.write_text("KEEP=1\n")
    out.chmod(0o644)
    assert m.main(["--output", str(out), "--contexts", "dev", "--force"]) == 0
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert "KEEP=1" in out.read_text()
    assert [p.name for p in tmp_path.iterdir()] == ["out.env"]


def test_main_write_failure_leaves_original_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_main(monkeypatch)
    out = tmp_path / "out.env"
    out.write_text("KEEP=1\n")

    def boom(src: Any, dst: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(m.os, "replace", boom)
    with pytest.raises(OSError):
        m.main(["--output", str(out), "--contexts", "dev", "--force"])
    assert out.read_text() == "KEEP=1\n"
    assert [p.name for p in tmp_path.iterdir()] == ["out.env"]


def test_collect_keyring_error_names_context_only() -> None:
    def locked(service: str, user: str) -> str | None:
        raise RuntimeError("secret ref v2:a:b")

    with pytest.raises(SystemExit) as exc:
        m.collect(_cfg(), {"prod"}, locked)
    msg = str(exc.value)
    assert "prod" in msg
    assert "v2:a:b" not in msg
