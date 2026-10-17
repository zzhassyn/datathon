"""Validated runtime paths, independent for every worktree."""

from dataclasses import dataclass
from pathlib import Path

from cashguard.core.ids import validate_job_id


def safe_child(root: Path, *parts: str) -> Path:
    root = root.expanduser().resolve()
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("path escapes runtime directory")
    return path


@dataclass(frozen=True)
class RuntimePaths:
    var_dir: Path

    @property
    def db_path(self) -> Path:
        return safe_child(self.var_dir, "db", "cashguard.sqlite3")

    @property
    def jobs_dir(self) -> Path:
        return safe_child(self.var_dir, "jobs")

    @property
    def logs_dir(self) -> Path:
        return safe_child(self.var_dir, "logs")

    def job_dir(self, job_id: str) -> Path:
        return safe_child(self.jobs_dir, validate_job_id(job_id))

    def input_path(self, job_id: str) -> Path:
        return safe_child(self.job_dir(job_id), "input.mov")

    def ensure(self) -> None:
        for path in (self.db_path.parent, self.jobs_dir, self.logs_dir):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
