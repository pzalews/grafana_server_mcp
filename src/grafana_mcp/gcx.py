"""The only place that spawns the gcx CLI.

Security boundary:
- Only allowlisted, read-only subcommands can be built (`ALLOWED_COMMANDS`), each with a fixed
  table of allowed flag names. User input only ever lands in `--name=value` flag values, never
  as a subcommand, flag name or positional argument.
- gcx runs via `asyncio.create_subprocess_exec` (no shell) with a minimal environment: no
  GRAFANA_* variables, tokens only reach gcx through the private config file at GCX_CONFIG.
- Error text is scrubbed of every configured secret before it leaves this module.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import shutil
import tempfile
import weakref
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from grafana_mcp.exceptions import GcxError, GcxTimeoutError, InvalidArgumentError
from grafana_mcp.gcx_config import GCX_SUBPROCESS_FLAGS

Command = Literal[
    "datasources list",
    "metrics list-names",
    "metrics labels",
    "metrics query",
    "logs labels",
    "logs query",
    "logs metrics",
]

FlagValue = str | int | list[str] | None

_ALLOWED_FLAGS: dict[str, frozenset[str]] = {
    "datasources list": frozenset({"type", "name"}),
    "metrics list-names": frozenset({"datasource", "match", "contains", "limit"}),
    "metrics labels": frozenset({"datasource", "label", "metric", "match"}),
    "metrics query": frozenset({"datasource", "expr", "since", "from", "to", "step", "time"}),
    "logs labels": frozenset({"datasource", "label", "query"}),
    "logs query": frozenset({"datasource", "expr", "since", "from", "to", "limit"}),
    "logs metrics": frozenset({"datasource", "expr", "since", "from", "to", "step"}),
}

ALLOWED_COMMANDS: frozenset[str] = frozenset(_ALLOWED_FLAGS)

_PASSTHROUGH_KEYS = (
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
)
_DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"
_ERROR_TAIL_CHARS = 2000
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def build_argv(
    gcx_path: str, env: str, command: Command, flags: Mapping[str, FlagValue]
) -> list[str]:
    """Build the gcx argv for an allowlisted command; every flag rendered as --name=value."""
    allowed = _ALLOWED_FLAGS.get(command)
    if allowed is None:
        raise InvalidArgumentError(f"gcx command not allowed: {command!r}")
    flag_args: list[str] = []
    for name, value in flags.items():
        if name not in allowed:
            raise InvalidArgumentError(f"flag {name!r} is not allowed for {command!r}")
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        flag_args.extend(f"--{name}={v}" for v in values)
    return [gcx_path, *command.split(), f"--context={env}", "-o", "json", *flag_args]


def _is_hint(line: str) -> bool:
    try:
        obj = json.loads(line)
    except ValueError:
        return False
    return isinstance(obj, dict) and obj.get("class") == "hint"


def parse_output(stdout: str) -> Any:
    """Parse gcx JSON output, skipping hint lines; fall back to JSON lines."""
    lines = [ln for ln in stdout.splitlines() if ln.strip() and not _is_hint(ln)]
    if not lines:
        raise GcxError("gcx returned no JSON output")
    try:
        return json.loads("\n".join(lines))
    except ValueError:
        pass
    try:
        return [json.loads(ln) for ln in lines]
    except ValueError:
        raise GcxError("gcx returned output that is not valid JSON") from None


def scrub(text: str, secrets: Iterable[str]) -> str:
    """Replace every non-empty secret in text with ***."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def _size(data: Any) -> int:
    return len(json.dumps(data))


def _largest(node: Any, kind: type) -> tuple[Any, Any, int] | None:
    """Find the largest non-empty value of `kind` in node: (parent, key, size)."""
    best: tuple[Any, Any, int] | None = None
    children: Iterable[tuple[Any, Any]]
    if isinstance(node, dict):
        children = node.items()
    elif isinstance(node, list):
        children = enumerate(node)
    else:
        return None
    for key, child in children:
        if isinstance(child, kind) and child:
            size = _size(child)
            if best is None or size > best[2]:
                best = (node, key, size)
        sub = _largest(child, kind)
        if sub is not None and (best is None or sub[2] > best[2]):
            best = sub
    return best


def _trim_list(lst: list[Any], overshoot: int) -> None:
    """Drop items from the end of lst until at least `overshoot` chars are removed."""
    removed = 0
    while lst and removed < overshoot:
        removed += _size(lst.pop()) + 2  # item plus ", " separator


def fit_result(data: Any, max_chars: int) -> dict[str, Any]:
    """Wrap data as a tool result, trimming lists (then strings) to fit max_chars."""
    if _size(data) <= max_chars:
        return {"result": data, "truncated": False}

    holder: list[Any] = [copy.deepcopy(data)]  # wrapper so the top level is trimmable too
    while (current := _size(holder[0])) > max_chars:
        overshoot = current - max_chars
        found = _largest(holder, list)
        if found is not None:
            parent, key, _ = found
            _trim_list(parent[key], overshoot)
            continue
        found = _largest(holder, str)
        if found is not None:
            parent, key, _ = found
            text: str = parent[key]
            keep = max(0, len(text) - overshoot - 3)
            parent[key] = text[:keep] + "..." if keep else ""
            continue
        holder[0] = json.dumps(holder[0])[: max(0, max_chars - 2)]  # last resort
        break

    return {
        "result": holder[0],
        "truncated": True,
        "note": (
            f"Result truncated to fit {max_chars} chars; narrow the time range, "
            "add label filters, or lower limit."
        ),
    }


def _gcx_error_message(*streams: str) -> str | None:
    """Return 'summary: details' from a gcx.error JSON line in any stream, if present."""
    for stream in streams:
        for line in stream.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("type") == "gcx.error":
                err = obj.get("error") or {}
                if not isinstance(err, dict):
                    return str(err)
                parts = [str(err[k]) for k in ("summary", "details") if err.get(k)]
                return ": ".join(parts) or "unknown gcx error"
    return None


async def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()


class GcxRunner:
    """Runs allowlisted gcx commands with a minimal environment and bounded concurrency."""

    def __init__(
        self,
        gcx_path: str,
        config_path: Path,
        timeout: float,
        max_concurrency: int,
        secrets: Sequence[str],
        passthrough_env: Mapping[str, str],
    ) -> None:
        self._gcx_path = gcx_path
        self._timeout = timeout
        self._secrets = [s for s in secrets if s]
        self._semaphore = asyncio.Semaphore(max_concurrency)
        home = tempfile.mkdtemp(prefix="gcx-home-")
        self._home_cleanup = weakref.finalize(self, shutil.rmtree, home, ignore_errors=True)
        self._env: dict[str, str] = {
            "PATH": os.environ.get("PATH", _DEFAULT_PATH),
            "HOME": home,
            "GCX_CONFIG": str(config_path),
            **GCX_SUBPROCESS_FLAGS,
        }
        for key in _PASSTHROUGH_KEYS:
            value = passthrough_env.get(key, "")
            if value:
                self._env[key] = value

    def close(self) -> None:
        """Remove the private HOME directory (also done automatically on garbage collection)."""
        self._home_cleanup()

    def _scrub(self, text: str) -> str:
        return scrub(text, self._secrets)

    async def run(self, env: str, command: Command, flags: Mapping[str, FlagValue]) -> Any:
        argv = build_argv(self._gcx_path, env, command, flags)
        async with self._semaphore:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._env,
                )
            except OSError as exc:
                raise GcxError(
                    f"could not start gcx: {exc.strerror or type(exc).__name__}"
                ) from None
            try:
                out_b, err_b = await asyncio.wait_for(proc.communicate(), self._timeout)
            except TimeoutError:
                await _kill(proc)
                raise GcxTimeoutError(
                    f"gcx {command} timed out after {self._timeout:g}s; "
                    "narrow the time range or query"
                ) from None
            except BaseException:  # e.g. the tool call was cancelled: never leave gcx running
                await _kill(proc)
                raise

        # Scrub before cutting so a secret can never be split by the tail limit.
        stdout = self._scrub(out_b.decode("utf-8", errors="replace"))
        stderr = self._scrub(_ANSI_RE.sub("", err_b.decode("utf-8", errors="replace")))
        if proc.returncode != 0:
            message = _gcx_error_message(stdout, stderr)
            if message is None:
                tail = (stderr.strip() or stdout.strip())[-_ERROR_TAIL_CHARS:]
                message = tail or "no output"
            raise GcxError(self._scrub(f"gcx {command} failed (exit {proc.returncode}): {message}"))

        try:
            result = parse_output(stdout)
        except GcxError as exc:
            raise GcxError(self._scrub(str(exc))) from None
        if isinstance(result, dict) and result.get("type") == "gcx.error":
            message = _gcx_error_message(json.dumps(result)) or "unknown gcx error"
            raise GcxError(self._scrub(f"gcx {command} failed: {message}"))
        return result
