"""Only completely verified, version-matched results are reusable."""

from pathlib import Path

from cashguard.jobs.db import Database
from cashguard.jobs.repo import JobRecord, JobRepository, _job


class ResultCache:
    def __init__(self, database: Database) -> None:
        self.db = database

    def get(
        self, sha256: str, pipeline_version: str, config_hash: str
    ) -> JobRecord | None:
        row = (
            self.db.connect()
            .execute(
                """SELECT j.* FROM result_cache c JOIN jobs j ON j.id=c.job_id
            WHERE c.sha256=? AND c.pipeline_version=? AND c.config_hash=?
            AND j.status='done' AND j.result_level='full'""",
                (sha256, pipeline_version, config_hash),
            )
            .fetchone()
        )
        job = _job(row)
        if job and job.jury_path and Path(job.jury_path).is_file():
            return job
        return None

    def put(self, job_id: str) -> bool:
        job = JobRepository(self.db).get_job(job_id)
        if not job or job.status != "done" or job.result_level != "full":
            return False
        self.db.connect().execute(
            "INSERT OR REPLACE INTO result_cache VALUES(?,?,?,?)",
            (job.sha256, job.pipeline_version, job.config_hash, job.id),
        )
        return True
