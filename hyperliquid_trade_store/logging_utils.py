from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def log_info(message: str) -> None:
    print(f"{utc_log_timestamp()} INFO {message}", file=sys.stderr, flush=True)


def log_error(message: str) -> None:
    print(f"{utc_log_timestamp()} ERROR {message}", file=sys.stderr, flush=True)


def utc_log_timestamp() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_path(path: Path | str | None) -> str:
    return "-" if path is None else str(path)


def format_list(values: list[str]) -> str:
    return ",".join(values) if values else "-"


def format_params(params: dict[str, Any]) -> str:
    if not params:
        return "-"
    return ",".join(f"{key}={value}" for key, value in params.items())
