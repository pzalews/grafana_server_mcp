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
    "resources get folders",
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
    # The resource kind is part of the fixed command, never user input.
    "resources get folders": frozenset(),
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


def _entry_size(key: Any, value: Any) -> int:
    # Serialized size of one list item / dict entry, including its ", " separator.
    key_size = len(json.dumps(key)) + 2 if isinstance(key, str) else 0
    return key_size + _size(value) + 2


def _cut_string(text: str, budget: int) -> str:
    """Longest prefix of text (plus "...") whose JSON encoding fits budget chars."""
    lo, hi = 0, len(text) - 1  # hi < len(text): always remove at least one char
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _size(text[:mid] + "...") <= budget:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "..." if lo else ""


def _shrink(holder: list[Any], overshoot: int) -> bool:
    """Remove at least `overshoot` serialized chars from holder[0], as precisely as possible.

    Walk down from the top: at each container look at the entry that would be removed next
    (the last list item, or the largest dict value). If removing it would drop at least the
    overshoot, descend into it (or cut it, if it is a string) so that a single huge series or
    stream is trimmed inside rather than dropped. Otherwise drop entries from the end of the
    current container until the overshoot is covered. Returns False if nothing could shrink.
    """
    target: Any = holder
    while True:
        if isinstance(target, list):
            if not target:
                return False
            key: Any = len(target) - 1
        elif isinstance(target, dict):
            if not target:
                return False
            key = max(target, key=lambda k: _size(target[k]))
        else:
            return False
        child = target[key]
        if _size(child) < overshoot:
            break
        if isinstance(child, (list, dict)) and child:
            target = child
            continue
        if isinstance(child, str) and child:
            target[key] = _cut_string(child, _size(child) - overshoot)
            return True
        break  # an atom at least as large as the overshoot: drop entries from the end
    if target is holder:  # never drop the whole result; replace an unshrinkable atom instead
        holder[0] = None
        return True
    removed = 0
    while target and removed < overshoot:
        if isinstance(target, list):
            removed += _entry_size(None, target.pop())
        else:
            k, v = target.popitem()
            removed += _entry_size(k, v)
    return True


def fit_result(data: Any, max_chars: int) -> dict[str, Any]:
    """Wrap data as a tool result; `json.dumps(result)` is guaranteed to fit max_chars."""
    if _size(data) <= max_chars:
        return {"result": data, "truncated": False}

    holder: list[Any] = [copy.deepcopy(data)]  # wrapper so the top level is trimmable too
    while (current := _size(holder[0])) > max_chars:
        if not _shrink(holder, current - max_chars):
            holder[0] = None
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


def _cap(message: str) -> str:
    if len(message) <= _ERROR_TAIL_CHARS:
        return message
    return message[:_ERROR_TAIL_CHARS] + "..."


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
            message = _cap(message)
            raise GcxError(self._scrub(f"gcx {command} failed (exit {proc.returncode}): {message}"))

        try:
            result = parse_output(stdout)
        except GcxError as exc:
            raise GcxError(self._scrub(str(exc))) from None
        if isinstance(result, dict) and result.get("type") == "gcx.error":
            message = _gcx_error_message(json.dumps(result)) or "unknown gcx error"
            message = _cap(message)
            raise GcxError(self._scrub(f"gcx {command} failed: {message}"))
        return result
