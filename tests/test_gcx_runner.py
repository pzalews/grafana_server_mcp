import asyncio
import json
import time
from pathlib import Path

import pytest

from grafana_mcp.exceptions import GcxError, GcxTimeoutError, InvalidArgumentError
from grafana_mcp.gcx import (
    ALLOWED_COMMANDS,
    GcxRunner,
    build_argv,
    fit_result,
    parse_output,
    scrub,
)
from tests.fakes import write_fake_gcx

FORBIDDEN_WORDS = (
    "api create update delete push pull login set unset edit use-context adaptive".split()
)


def _runner(
    gcx: Path,
    tmp_path: Path,
    *,
    timeout: float = 10,
    max_concurrency: int = 4,
    secrets: tuple[str, ...] = (),
    passthrough_env: dict[str, str] | None = None,
) -> GcxRunner:
    return GcxRunner(
        gcx_path=str(gcx),
        config_path=tmp_path / "config.yaml",
        timeout=timeout,
        max_concurrency=max_concurrency,
        secrets=secrets,
        passthrough_env=passthrough_env or {},
    )


# --- build_argv -------------------------------------------------------------------------


def test_build_argv_metrics_query() -> None:
    argv = build_argv(
        "gcx",
        "prod",
        "metrics query",
        {"datasource": "abc", "expr": "up", "since": "1h", "from": None},
    )
    assert argv == [
        "gcx",
        "metrics",
        "query",
        "--context=prod",
        "-o",
        "json",
        "--datasource=abc",
        "--expr=up",
        "--since=1h",
    ]


def test_build_argv_list_and_int_flags() -> None:
    argv = build_argv("gcx", "dev", "metrics list-names", {"match": ["up", 'job="a"'], "limit": 5})
    assert argv[6:] == ["--match=up", '--match=job="a"', "--limit=5"]


@pytest.mark.parametrize("command", ["api", "dashboards delete", "config set", "metrics"])
def test_build_argv_rejects_unknown_command(command: str) -> None:
    with pytest.raises(InvalidArgumentError):
        build_argv("gcx", "prod", command, {})  # type: ignore[arg-type]


@pytest.mark.parametrize("flags", [{"jq": "."}, {"config": "/etc/x"}, {"label": "job"}])
def test_build_argv_rejects_unknown_flag(flags: dict[str, str]) -> None:
    with pytest.raises(InvalidArgumentError):
        build_argv("gcx", "prod", "metrics query", flags)


def test_allowlist_is_read_only() -> None:
    assert ALLOWED_COMMANDS
    for cmd in ALLOWED_COMMANDS:
        words = cmd.split()
        assert len(words) == 2
        for bad in FORBIDDEN_WORDS:
            assert bad not in words, cmd


def test_hostile_expr_is_single_flag_value() -> None:
    expr = '-{app="x"} |= "a b"\n| json'
    argv = build_argv("gcx", "prod", "logs query", {"datasource": "abc", "expr": expr})
    expected = '--expr=-{app="x"} |= "a b"\n| json'
    assert argv.count(expected) == 1
    assert expr not in argv  # never a bare positional


# --- parse_output -----------------------------------------------------------------------


def test_parse_output_skips_hint() -> None:
    assert parse_output('{"class":"hint","summary":"x"}\n{"data":1}') == {"data": 1}


def test_parse_output_multiline_document() -> None:
    assert parse_output('{\n  "a": [\n    1\n  ]\n}\n') == {"a": [1]}


def test_parse_output_json_lines() -> None:
    assert parse_output('{"a":1}\n{"a":2}\n') == [{"a": 1}, {"a": 2}]


@pytest.mark.parametrize("text", ["", "not json", '{"class":"hint","summary":"x"}\n'])
def test_parse_output_raises_on_garbage(text: str) -> None:
    with pytest.raises(GcxError):
        parse_output(text)


# --- scrub ------------------------------------------------------------------------------


def test_scrub() -> None:
    assert scrub("bad token glsa_abc", ["glsa_abc", ""]) == "bad token ***"


# --- fit_result -------------------------------------------------------------------------


def test_fit_result_untouched_when_small() -> None:
    assert fit_result({"a": [1, 2]}, 100) == {"result": {"a": [1, 2]}, "truncated": False}


def test_fit_result_trims_list() -> None:
    data = [{"i": i, "line": f"log line number {i}"} for i in range(1000)]
    out = fit_result(data, 2000)
    assert out["truncated"] is True
    assert "2000" in out["note"]
    assert len(json.dumps(out)) <= 2300
    assert out["result"] == data[: len(out["result"])]
    assert len(out["result"]) > 0
    assert len(data) == 1000  # input not mutated


def test_fit_result_trims_largest_list_in_dict() -> None:
    data = {"small": [1, 2, 3], "big": list(range(5000)), "meta": "x"}
    out = fit_result(data, 1000)
    assert out["truncated"] is True
    assert out["result"]["small"] == [1, 2, 3]
    assert out["result"]["meta"] == "x"
    assert out["result"]["big"] == list(range(len(out["result"]["big"])))
    assert len(json.dumps(out["result"])) <= 1000


def test_fit_result_cuts_string_as_last_resort() -> None:
    out = fit_result({"s": "y" * 10_000}, 500)
    assert out["truncated"] is True
    assert len(json.dumps(out["result"])) <= 500
    assert out["result"]["s"].startswith("yyy")


def test_fit_result_single_prometheus_series_keeps_values() -> None:
    values = [[1_700_000_000 + i * 15, str(i)] for i in range(20_000)]
    data = {
        "data": {
            "resultType": "matrix",
            "result": [{"metric": {"__name__": "up", "job": "a"}, "values": values}],
        }
    }
    out = fit_result(data, 50_000)
    assert out["truncated"] is True
    assert len(json.dumps(out["result"])) <= 50_000
    series = out["result"]["data"]["result"]
    assert len(series) == 1
    assert series[0]["metric"] == {"__name__": "up", "job": "a"}
    kept = series[0]["values"]
    assert 0 < len(kept) < len(values)
    assert kept == values[: len(kept)]
    assert len(json.dumps(out["result"])) > 40_000  # trimmed close to the cap, not emptied


def test_fit_result_single_loki_stream_keeps_values() -> None:
    values = [
        [str(1_700_000_000_000_000_000 + i), f"log line {i} " + "x" * 40] for i in range(5000)
    ]
    data = [{"stream": {"app": "x"}, "values": values}]
    out = fit_result(data, 20_000)
    assert out["truncated"] is True
    assert len(json.dumps(out["result"])) <= 20_000
    assert len(out["result"]) == 1
    kept = out["result"][0]["values"]
    assert 0 < len(kept) < len(values)
    assert kept == values[: len(kept)]


def test_fit_result_many_keys_dict_fits_cap() -> None:
    data = {f"key{i}": "" for i in range(20_000)}
    out = fit_result(data, 50_000)
    assert out["truncated"] is True
    assert isinstance(out["result"], dict)
    assert 0 < len(out["result"]) < len(data)
    assert len(json.dumps(out["result"])) <= 50_000
    assert len(json.dumps(out)) <= 50_000 + 200


def test_fit_result_string_with_escapes_fits_cap() -> None:
    out = fit_result({"s": '"' * 10_000}, 500)
    assert len(json.dumps(out["result"])) <= 500
    assert out["result"]["s"].startswith('"')


# --- GcxRunner --------------------------------------------------------------------------


async def test_run_ok(tmp_path: Path) -> None:
    runner = _runner(write_fake_gcx(tmp_path, "ok"), tmp_path)
    assert await runner.run("dev", "datasources list", {}) == {"data": [1, 2, 3]}


async def test_run_skips_hint(tmp_path: Path) -> None:
    runner = _runner(write_fake_gcx(tmp_path, "hint"), tmp_path)
    assert await runner.run("dev", "datasources list", {}) == {"data": "after-hint"}


async def test_run_passes_built_argv(tmp_path: Path) -> None:
    runner = _runner(write_fake_gcx(tmp_path, "echo_argv"), tmp_path)
    out = await runner.run("prod", "logs query", {"datasource": "abc", "expr": "-x", "limit": 5})
    assert out == [
        "logs",
        "query",
        "--context=prod",
        "-o",
        "json",
        "--datasource=abc",
        "--expr=-x",
        "--limit=5",
    ]


async def test_run_rejects_disallowed_command(tmp_path: Path) -> None:
    runner = _runner(write_fake_gcx(tmp_path, "ok"), tmp_path)
    with pytest.raises(InvalidArgumentError):
        await runner.run("dev", "api", {})  # type: ignore[arg-type]


async def test_error_json_surfaces_summary(tmp_path: Path) -> None:
    runner = _runner(write_fake_gcx(tmp_path, "error_json"), tmp_path)
    with pytest.raises(GcxError) as exc:
        await runner.run("dev", "datasources list", {})
    assert "Datasource not found" in str(exc.value)
    assert "uid xyz" in str(exc.value)


async def test_error_text_is_scrubbed(tmp_path: Path) -> None:
    secret = "glsa_supersecret_123"
    runner = _runner(
        write_fake_gcx(tmp_path, "stderr", secret=secret), tmp_path, secrets=(secret, "")
    )
    with pytest.raises(GcxError) as exc:
        await runner.run("dev", "datasources list", {})
    assert secret not in str(exc.value)
    assert "***" in str(exc.value)


async def test_timeout_kills_process(tmp_path: Path) -> None:
    runner = _runner(write_fake_gcx(tmp_path, "sleep", sleep=30), tmp_path, timeout=0.5)
    start = time.monotonic()
    with pytest.raises(GcxTimeoutError):
        await runner.run("dev", "datasources list", {})
    assert time.monotonic() - start < 3


async def test_subprocess_env_is_minimal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAFANA_TOKEN", "t1")
    monkeypatch.setenv("GRAFANA_SERVER", "https://grafana.example.com")
    monkeypatch.setenv("GRAFANA_DEV_TOKEN", "t2")
    monkeypatch.setenv("HTTPS_PROXY", "http://from-os-environ:3128")
    gcx = write_fake_gcx(tmp_path, "echo_env")

    runner = _runner(gcx, tmp_path)
    child = await runner.run("dev", "datasources list", {})
    for key in ("GRAFANA_TOKEN", "GRAFANA_SERVER", "GRAFANA_DEV_TOKEN", "HTTPS_PROXY"):
        assert key not in child
    assert child["GCX_CONFIG"] == str(tmp_path / "config.yaml")
    assert child["GCX_KEYCHAIN"] == "off"
    assert child["GCX_TELEMETRY"] == "disabled"
    assert child["HOME"] != str(Path.home())
    assert Path(child["HOME"]).is_dir()
    allowed = {"PATH", "HOME", "GCX_CONFIG", "GCX_KEYCHAIN", "GCX_TELEMETRY"}
    # Some platforms inject a handful of vars at exec time (e.g. LC_CTYPE on macOS).
    assert set(child) - allowed <= {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"}
    runner.close()
    assert not Path(child["HOME"]).exists()

    passthrough = {
        "HTTPS_PROXY": "http://proxy.example.com:3128",
        "no_proxy": "localhost",
        "SSL_CERT_FILE": "",
        "GRAFANA_TOKEN": "t3",
    }
    child = await _runner(gcx, tmp_path, passthrough_env=passthrough).run(
        "dev", "datasources list", {}
    )
    assert child["HTTPS_PROXY"] == "http://proxy.example.com:3128"
    assert child["no_proxy"] == "localhost"
    assert "SSL_CERT_FILE" not in child
    assert "GRAFANA_TOKEN" not in child


async def test_concurrency_limit(tmp_path: Path) -> None:
    runner = _runner(write_fake_gcx(tmp_path, "sleep", sleep=0.3), tmp_path, max_concurrency=2)
    start = time.monotonic()
    await asyncio.gather(*(runner.run("dev", "datasources list", {}) for _ in range(8)))
    assert time.monotonic() - start >= 1.1


async def test_error_text_strips_ansi(tmp_path: Path) -> None:
    runner = _runner(write_fake_gcx(tmp_path, "stderr", secret="\x1b[92mtok\x1b[0m"), tmp_path)
    with pytest.raises(GcxError) as exc:
        await runner.run("dev", "datasources list", {})
    assert "\x1b" not in str(exc.value)
    assert "tok" in str(exc.value)


async def test_missing_binary_is_gcx_error(tmp_path: Path) -> None:
    runner = _runner(tmp_path / "does-not-exist", tmp_path)
    with pytest.raises(GcxError):
        await runner.run("dev", "datasources list", {})


async def test_cancel_kills_process(tmp_path: Path) -> None:
    runner = _runner(write_fake_gcx(tmp_path, "sleep", sleep=30), tmp_path, max_concurrency=1)
    task = asyncio.create_task(runner.run("dev", "datasources list", {}))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The semaphore slot (max 1) was released: the same runner serves the next call promptly.
    write_fake_gcx(tmp_path, "ok")  # overwrites the same executable path
    start = time.monotonic()
    assert await runner.run("dev", "datasources list", {}) == {"data": [1, 2, 3]}
    assert time.monotonic() - start < 3


async def test_error_message_is_capped(tmp_path: Path) -> None:
    runner = _runner(write_fake_gcx(tmp_path, "error_huge"), tmp_path)
    with pytest.raises(GcxError) as exc:
        await runner.run("dev", "datasources list", {})
    assert "Boom" in str(exc.value)
    assert str(exc.value).endswith("...")
    assert len(str(exc.value)) < 2500


async def test_error_message_is_capped_on_exit_zero(tmp_path: Path) -> None:
    runner = _runner(write_fake_gcx(tmp_path, "error_huge_ok"), tmp_path)
    with pytest.raises(GcxError) as exc:
        await runner.run("dev", "datasources list", {})
    assert str(exc.value).endswith("...")
    assert len(str(exc.value)) < 2500


async def test_successful_result_is_scrubbed(tmp_path: Path) -> None:
    secret = "glsa_supersecret_123"
    runner = _runner(
        write_fake_gcx(tmp_path, "ok_secret", secret=secret), tmp_path, secrets=(secret, "")
    )
    result = await runner.run("dev", "datasources list", {})
    assert secret not in str(result)
    assert "***" in str(result)
