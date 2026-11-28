"""Terminal status snapshots and ASCII art for ShellRPG."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
import time


GATE_ART = (
    r"   /\       /\    ShellRPG",
    r"  |__|_|_|_|__|   Pangae",
    r"  |   |___|   |",
)


def _read_regular(path: Path, limit: int, max_age: int | None = None) -> bytes:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("status file is not regular")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("invalid status file")
        if max_age is not None and not 0 <= time.time() - info.st_mtime <= max_age:
            raise ValueError("status file is stale")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("status file is too large")
    return data


def updatewulf_line() -> str:
    root = Path("/run/updatewulf-prompt")
    try:
        timestamp = int(_read_regular(root / "heartbeat.txt", 20))
        if not 0 <= time.time() - timestamp <= 180:
            return ""
        value = _read_regular(root / "fleet-prompt.txt", 128).decode("ascii").rstrip("\n")
    except (OSError, ValueError):
        return ""
    if re.fullmatch(r"\[ UpdateWulf ::: [A-Za-z0-9-]{1,32} [ -~]{1,60} \]", value):
        return value
    return ""


def pangae_line(status_file: Path, columns: int, rows: int) -> str:
    if rows < 2 or columns < 17:
        return ""
    try:
        status = json.loads(_read_regular(status_file, 256, max_age=20))
        if not isinstance(status, dict):
            return ""
        schema, tick, count = (status.get(field) for field in ("schema", "world_tick", "characters"))
        mode = status.get("mode")
        if not all(type(value) is int for value in (schema, tick, count)) or schema != 1 or min(tick, count) < 0:
            return ""
        if mode not in ("local", "irc"):
            return ""
    except (OSError, ValueError):
        return ""
    label = "IRC" if mode == "irc" else "lokal"
    choices = (
        f"idleQuest | {label} | Runde {tick} | {count} Charaktere",
        f"idleQuest | {label} | R{tick} C{count}",
        f"idleQuest {label}",
    )
    return next((value for value in choices if len(value) < columns), "")


def pangae_prompt_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="idlequest prompt")
    parser.add_argument("--status-file", type=Path, default=os.environ.get("IDLEQUEST_STATUS_FILE", "/run/idlequest/prompt.json"))
    parser.add_argument("--columns", type=int)
    parser.add_argument("--rows", type=int)
    args = parser.parse_args(argv)
    columns, rows = args.columns, args.rows
    if columns is None and rows is None:
        try:
            columns, rows = os.get_terminal_size(sys.stdout.fileno())
        except OSError:
            return 0
    elif columns is None or rows is None:
        parser.error("--columns and --rows must be set together")
    if line := pangae_line(args.status_file, columns, rows):
        print(line)
    return 0


def ansi_colors_enabled() -> bool:
    return "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
