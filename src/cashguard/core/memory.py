"""Resource measurements without loading model libraries."""

import shutil
from dataclasses import dataclass
from pathlib import Path

import psutil


@dataclass(frozen=True)
class ResourceSnapshot:
    free_memory_bytes: int
    rss_bytes: int
    free_disk_bytes: int


def resource_snapshot(path: Path, pid: int | None = None) -> ResourceSnapshot:
    return ResourceSnapshot(
        psutil.virtual_memory().available,
        psutil.Process(pid).memory_info().rss,
        shutil.disk_usage(path).free,
    )


def pid_alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True
