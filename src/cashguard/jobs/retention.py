"""Delete only expired terminal jobs, preserving live and linked result owners."""

import shutil
import time

from cashguard.core.ids import valid_job_id
from cashguard.core.paths import RuntimePaths
from cashguard.jobs.repo import JobRepository


def purge_expired(
    repo: JobRepository,
    paths: RuntimePaths,
    retention_hours: float = 24,
    now: float | None = None,
) -> list[str]:
    if retention_hours < 0:
        raise ValueError("retention cannot be negative")
    if retention_hours == 0:
        return []
    now = time.time() if now is None else now
    cutoff = now - retention_hours * 3600
    deleted: list[str] = []
    # A short writer lock prevents new deduplicated children being inserted while
    # deciding whether the canonical result directory is still referenced.
    with repo.db.transaction() as connection:
        rows = connection.execute(
            """SELECT id FROM jobs WHERE status IN ('done','failed')
            AND finished_at<? ORDER BY linked_to IS NULL,finished_at""",
            (cutoff,),
        ).fetchall()
        for row in rows:
            job_id = row["id"]
            if connection.execute(
                "SELECT 1 FROM jobs WHERE linked_to=? LIMIT 1", (job_id,)
            ).fetchone():
                continue
            connection.execute(
                "DELETE FROM jobs WHERE id=? AND status IN ('done','failed')", (job_id,)
            )
            deleted.append(job_id)
    # Commit deletion before removing files: a crash may leave an orphan file,
    # but it must never leave a visible terminal row whose result disappeared.
    for job_id in deleted:
        path = paths.job_dir(job_id)
        trash = paths.jobs_dir / f".expired-{job_id}"
        if path.exists() and not trash.exists():
            try:
                path.rename(trash)
            except FileNotFoundError:
                pass  # Another sweeper already completed this deletion.
        if trash.is_symlink():
            trash.unlink()
        elif trash.is_dir():
            try:
                shutil.rmtree(trash)
            except FileNotFoundError:
                pass
    # Retry interrupted cleanup and uploads abandoned before DB insertion.
    # Ten minutes exceeds the maximum upload budget, so a live upload cannot
    # be mistaken for an orphan even with a short test retention setting.
    orphan_cutoff = now - max(retention_hours * 3600, 600)
    if paths.jobs_dir.is_dir():
        for path in paths.jobs_dir.iterdir():
            job_id = path.name.removeprefix(".expired-")
            if (
                valid_job_id(job_id)
                and not path.is_symlink()
                and path.is_dir()
                and (
                    path.name.startswith(".expired-")
                    or path.stat().st_mtime < orphan_cutoff
                )
                and repo.get_job(job_id) is None
            ):
                try:
                    shutil.rmtree(path)
                except FileNotFoundError:
                    pass
    return deleted
