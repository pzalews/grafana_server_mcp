"""A fake `gcx` executable for runner tests.

The script is written into a temp dir and run via the current interpreter. Its behaviour is
driven by the env var FAKE_GCX_MODE. Because the runner gives the child a minimal environment
(FAKE_GCX_* is not passed through), the mode, secret and sleep time are baked into the script
as defaults when it is written.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

_SCRIPT = """#!{python}
import json
import os
import sys
import time

mode = os.environ.get("FAKE_GCX_MODE", {mode!r})
secret = os.environ.get("FAKE_GCX_SECRET", {secret!r})
sleep_s = float(os.environ.get("FAKE_GCX_SLEEP", {sleep!r}))

if mode == "ok":
    print(json.dumps({{"data": [1, 2, 3]}}))
elif mode == "hint":
    print(json.dumps({{"class": "hint", "summary": "try something"}}))
    print(json.dumps({{"data": "after-hint"}}))
elif mode == "error_json":
    print(json.dumps({{
        "type": "gcx.error",
        "schema_version": "1",
        "error": {{"summary": "Datasource not found", "exitCode": 1, "details": "uid xyz"}},
    }}))
    sys.exit(1)
elif mode == "stderr":
    sys.stderr.write("request failed: Authorization: Bearer " + secret + "\\n")
    sys.exit(2)
elif mode == "sleep":
    time.sleep(sleep_s)
    print(json.dumps({{"slept": sleep_s}}))
elif mode == "echo_argv":
    print(json.dumps(sys.argv[1:]))
elif mode == "echo_env":
    print(json.dumps(dict(os.environ)))
else:
    sys.stderr.write("unknown mode " + mode + "\\n")
    sys.exit(3)
"""


def write_fake_gcx(directory: Path, mode: str = "ok", secret: str = "", sleep: float = 0.0) -> Path:
    """Write an executable fake gcx into `directory` and return its path."""
    path = directory / "fake-gcx"
    path.write_text(
        _SCRIPT.format(python=sys.executable, mode=mode, secret=secret, sleep=str(sleep))
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path
