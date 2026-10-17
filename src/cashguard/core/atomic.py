"""Durable atomic publication: temporary file, fsync, replace, directory fsync."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def atomic_write_json(path: Path, value: Any) -> Path:
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return atomic_write_bytes(path, data.encode("utf-8"))


def durable_replace(source: Path, target: Path) -> None:
    """Publish an already-streamed file without loading it into memory."""
    with Path(source).open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(source, target)
    fsync_directory(Path(target).parent)
