"""Thin wrapper over the capcut-cli binary.

Every call is one process, no shell, JSON out. Mirrors the upstream Python
client's contract but stays in-repo so the exact behaviours this pipeline
depends on (batch op shapes, warning strings) are pinned here.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

PINNED_VERSION = "0.25.0"


class CliNotFound(Exception):
    pass


class CommandError(Exception):
    def __init__(self, cmd: list[str], status: int, data: Any, stdout: str, stderr: str):
        self.cmd, self.status, self.data, self.stdout, self.stderr = cmd, status, data, stdout, stderr
        msg = data.get("error") if isinstance(data, dict) and data.get("error") else (stderr.strip() or stdout.strip())
        super().__init__(f"{' '.join(cmd[:3])} failed ({status}): {msg}")


@dataclass
class Result:
    ok: bool
    status: int
    data: Any
    stdout: str
    stderr: str


def cli_argv() -> list[str]:
    override = os.environ.get("CAPCUT_CLI")
    if override:
        return shlex.split(override)
    exe = shutil.which("capcut")
    if exe is None:
        raise CliNotFound("capcut-cli not found: npm install -g capcut-cli@" + PINNED_VERSION)
    return [exe]


def run_raw(*args: str, stdin: str | None = None, **flags: Any) -> Result:
    cmd = cli_argv() + list(args)
    for k, v in flags.items():
        flag = "--" + k.replace("_", "-")
        if v is None or v is False:
            continue
        if v is True:
            cmd.append(flag)
        elif isinstance(v, (list, tuple)):
            for item in v:
                cmd += [flag, str(item)]
        else:
            cmd += [flag, str(v)]
    proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True, check=False)
    data: Any = None
    out = proc.stdout.strip()
    if out:
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            # Some commands print multiple JSON documents; keep the last one.
            for line in reversed(out.splitlines()):
                try:
                    data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
    return Result(proc.returncode == 0, proc.returncode, data, proc.stdout, proc.stderr)


def run(*args: str, stdin: str | None = None, **flags: Any) -> Any:
    r = run_raw(*args, stdin=stdin, **flags)
    if not r.ok:
        raise CommandError(cli_argv() + list(args), r.status, r.data, r.stdout, r.stderr)
    return r.data


def version() -> str:
    r = run_raw("--version")
    return (r.stdout or "").strip()


def check_version() -> None:
    v = version()
    if v != PINNED_VERSION:
        raise CommandError(["capcut", "--version"], 1, {"error": f"capcut-cli {v} installed, pipeline pinned to {PINNED_VERSION}"}, v, "")
