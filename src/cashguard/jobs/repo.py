"""Durable jobs and CAS transitions. Internal rows are not public API contracts."""

import json
import math
import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from cashguard.core.budget import DeadlineTiers, load_deadline_offsets
from cashguard.core.ids import new_job_id, validate_job_id
from cashguard.core.memory import pid_alive as process_alive
from cashguard.jobs.db import Database


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class JobRecord:
    id: str
    source: str
    priority: int
    client_ip: str | None
    status: str
    stage: str
    perception_level: str
    result_level: str | None
    progress: float
    stage_code: str
    stages_json: str
    received_at: float
    vision_target: float
    vlm_start_cutoff: float
    vlm_stop: float
    finalize_at: float
    perception_hard_stop: float
    started_at: float | None
    provisional_at: float | None
    finished_at: float | None
    attempts_perception: int
    attempts_vlm: int
    lease_owner: str | None
    lease_pid: int | None
    lease_token: str | None
    alive_at: float | None
    progress_at: float | None
    input_path: str
    sha256: str
    size_bytes: int
    duration_sec: float
    width: int
    height: int
    rotation: int
    codec: str
    needs_transcode: bool
    verdict: str | None
    n_violations: int
    linked_to: str | None
    error_code: str | None
    error_msg: str | None
    timings_json: str
    degradations_json: str
    metadata_json: str
    pipeline_version: str
    config_hash: str
    jury_path: str | None
    ui_result_path: str | None

    @property
    def job_id(self) -> str:
        return self.id

    @property
    def public_status(self) -> str:
        return "processing" if self.status in ("queued", "running") else self.status

    @property
    def metadata(self) -> dict[str, Any]:
        return json.loads(self.metadata_json)

    @property
    def degradations(self) -> list[Any]:
        return json.loads(self.degradations_json)

    @property
    def tiers(self) -> DeadlineTiers:
        return DeadlineTiers(
            self.received_at,
            self.vision_target,
            self.vlm_start_cutoff,
            self.vlm_stop,
            self.finalize_at,
            self.perception_hard_stop,
        )

    def to_dict(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


def _job(row: sqlite3.Row | None) -> JobRecord | None:
    return JobRecord(**dict(row)) if row is not None else None


class JobRepository:
    @staticmethod
    def _snapshot_where(snapshot: JobRecord | None) -> tuple[str, tuple[Any, ...]]:
        """Fence a previously read row against retries and new publications."""
        if snapshot is None:
            return "", ()
        columns = (
            "status",
            "stage",
            "lease_owner",
            "lease_pid",
            "lease_token",
            "attempts_perception",
            "attempts_vlm",
            "provisional_at",
            "jury_path",
            "ui_result_path",
        )
        return " AND " + " AND ".join(f"{column} IS ?" for column in columns), tuple(
            getattr(snapshot, column) for column in columns
        )

    def __init__(
        self, database: Database | Path | str, *, deadlines_path: Path | None = None
    ) -> None:
        self.db = database if isinstance(database, Database) else Database(database)
        self.deadline_offsets = load_deadline_offsets(deadlines_path)

    def get_job(self, job_id: str) -> JobRecord | None:
        return _job(
            self.db.connect().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        )

    def resolve_job(self, job_id: str) -> JobRecord | None:
        job = self.get_job(job_id)
        return self.get_job(job.linked_to) if job and job.linked_to else job

    def create_job(
        self,
        *,
        input_path: str | Path,
        sha256: str,
        job_id: str | None = None,
        received_at: float | None = None,
        duration_sec: float = 0,
        source: str = "jury",
        metadata: dict[str, Any] | None = None,
        pipeline_version: str = "stub-v1",
        config_hash: str = "default",
        deduplicate: bool = True,
        priority: int | None = None,
        client_ip: str | None = None,
        size_bytes: int = 0,
        width: int = 0,
        height: int = 0,
        codec: str = "",
        rotation: int = 0,
        needs_transcode: bool = False,
    ) -> JobRecord:
        job_id = validate_job_id(job_id or new_job_id())
        now = time.time() if received_at is None else received_at
        tiers = DeadlineTiers.from_received_at(now, self.deadline_offsets)
        if not math.isfinite(duration_sec) or duration_sec < 0:
            raise ValueError("invalid video duration")
        values: dict[str, Any] = {
            "id": job_id,
            "source": source,
            "priority": priority if priority is not None else (0 if source == "jury" else 1),
            "client_ip": client_ip,
            "status": "queued",
            "received_at": now,
            "input_path": str(input_path),
            "sha256": sha256,
            "duration_sec": duration_sec,
            "metadata_json": _json(metadata or {}),
            "pipeline_version": pipeline_version,
            "config_hash": config_hash,
            "size_bytes": size_bytes,
            "width": width,
            "height": height,
            "codec": codec,
            "rotation": rotation,
            "needs_transcode": int(needs_transcode),
        }
        values.update({key: value for key, value in tiers.as_dict().items() if key != "t0"})
        with self.db.transaction() as connection:
            parent = None
            if deduplicate:
                parent = connection.execute(
                    """SELECT * FROM jobs WHERE sha256=? AND pipeline_version=?
                    AND config_hash=? AND linked_to IS NULL AND status IN ('queued','running')
                    ORDER BY received_at,id LIMIT 1""",
                    (sha256, pipeline_version, config_hash),
                ).fetchone()
                if parent is None:
                    parent = connection.execute(
                        """SELECT j.* FROM result_cache c JOIN jobs j ON j.id=c.job_id
                        WHERE c.sha256=? AND c.pipeline_version=? AND c.config_hash=?
                        AND j.status='done' AND j.result_level='full'""",
                        (sha256, pipeline_version, config_hash),
                    ).fetchone()
                if (
                    parent is not None
                    and parent["status"] == "done"
                    and (
                        not Path(parent["jury_path"] or "").is_file()
                        or (
                            parent["ui_result_path"]
                            and not Path(parent["ui_result_path"]).is_file()
                        )
                    )
                ):
                    parent = None
            if parent is not None:
                values["linked_to"] = parent["id"]
                if parent["status"] in ("queued", "running"):
                    connection.execute(
                        "UPDATE jobs SET priority=MIN(priority,?) WHERE id=?",
                        (values["priority"], parent["id"]),
                    )
                if parent["status"] == "done":
                    for name in (
                        "status",
                        "result_level",
                        "verdict",
                        "n_violations",
                        "jury_path",
                        "ui_result_path",
                        "degradations_json",
                    ):
                        values[name] = parent[name]
                    values.update(finished_at=now, progress=1.0, stage_code="done")
            columns = ",".join(values)
            marks = ",".join("?" for _ in values)
            row = connection.execute(
                f"INSERT INTO jobs ({columns}) VALUES ({marks}) RETURNING *",
                tuple(values.values()),
            ).fetchone()
        result = _job(row)
        if result is None:  # инвариант: строка только что прочитана/записана в транзакции
            raise RuntimeError("нарушен инвариант очереди: result is None")
        return result

    def link_job(self, job_id: str, parent_id: str) -> bool:
        return (
            self.db.connect()
            .execute(
                """UPDATE jobs SET linked_to=? WHERE id=? AND status='queued'
            AND linked_to IS NULL AND id!=? AND EXISTS(SELECT 1 FROM jobs p WHERE p.id=?
            AND p.linked_to IS NULL AND p.status IN ('queued','running')
            AND p.sha256=jobs.sha256 AND p.pipeline_version=jobs.pipeline_version AND p.config_hash=jobs.config_hash)
            AND NOT EXISTS(SELECT 1 FROM jobs child WHERE child.linked_to=jobs.id)""",
                (parent_id, job_id, parent_id, parent_id),
            )
            .rowcount
            == 1
        )

    def claim_next(
        self,
        owner: str,
        pid: int | None = None,
        stage: str = "perception",
        now: float | None = None,
    ) -> JobRecord | None:
        now = time.time() if now is None else now
        if stage not in ("perception", "vlm"):
            raise ValueError("unknown worker stage")
        counter = f"attempts_{stage}"
        with self.db.transaction() as connection:
            row = connection.execute(
                f"""UPDATE jobs SET status='running',lease_owner=?,lease_pid=?,lease_token=?,
                alive_at=?,progress_at=?,started_at=COALESCE(started_at,?),stage_code=?,
                {counter}={counter}+1,
                perception_level=CASE WHEN stage='perception' AND ? > received_at+200 THEN 'min'
                  WHEN stage='perception' AND attempts_perception>0 THEN 'fast' ELSE perception_level END
                WHERE id=(SELECT id FROM jobs WHERE stage=? AND status='queued' AND linked_to IS NULL
                ORDER BY priority,received_at,id LIMIT 1) AND status='queued' RETURNING *""",
                (
                    owner,
                    pid or os.getpid(),
                    new_job_id(),
                    now,
                    now,
                    now,
                    stage,
                    now,
                    stage,
                ),
            ).fetchone()
        return _job(row)

    def beat_alive(
        self, owner: str, now: float | None = None, *, lease_token: str | None = None
    ) -> int:
        now = time.time() if now is None else now
        return (
            self.db.connect()
            .execute(
                "UPDATE jobs SET alive_at=? WHERE lease_owner=? AND status='running' AND lease_token=?",
                (now, owner, lease_token),
            )
            .rowcount
        )

    def beat_progress(
        self,
        job_id: str,
        owner: str,
        now: float | None = None,
        *,
        lease_token: str | None = None,
    ) -> bool:
        now = time.time() if now is None else now
        with self.db.transaction() as connection:
            updated = connection.execute(
                """UPDATE jobs SET progress_at=? WHERE id=? AND status='running'
                AND lease_owner=? AND lease_token=? AND (progress_at IS NULL OR progress_at<=?)""",
                (now, job_id, owner, lease_token, now - 0.5),
            ).rowcount
            if updated:
                connection.execute("UPDATE workers SET progress_at=? WHERE name=?", (now, owner))
        return updated == 1

    def report_progress(
        self,
        job_id: str,
        owner: str,
        stage_code: str,
        frac: float,
        msg: str | None = None,
        now: float | None = None,
        *,
        lease_token: str | None = None,
    ) -> bool:
        if not math.isfinite(frac) or not 0 <= frac <= 1:
            raise ValueError("progress must be between 0 and 1")
        now = time.time() if now is None else now
        with self.db.transaction() as connection:
            updated = connection.execute(
                """UPDATE jobs SET stage_code=?,progress=?,progress_at=?
                WHERE id=? AND status='running' AND lease_owner=? AND lease_token=? AND (progress_at IS NULL OR progress_at<=?)""",
                (stage_code, frac, now, job_id, owner, lease_token, now - 0.5),
            ).rowcount
            if updated:
                connection.execute("UPDATE workers SET progress_at=? WHERE name=?", (now, owner))
                connection.execute(
                    "INSERT INTO job_events(job_id,at,kind,data_json) VALUES(?,?,?,?)",
                    (
                        job_id,
                        now,
                        "progress",
                        _json({"stage": stage_code, "progress": frac, "note_ru": msg}),
                    ),
                )
        return updated == 1

    @staticmethod
    def _paths(jury_path: str | Path, ui_result_path: str | Path | None) -> tuple[str, str | None]:
        if not Path(jury_path).is_file():
            raise ValueError("jury result must be published before database transition")
        if ui_result_path is not None and not Path(ui_result_path).is_file():
            raise ValueError("UI result must be published before database transition")
        return str(jury_path), str(ui_result_path) if ui_result_path is not None else None

    def set_provisional(
        self,
        job_id: str,
        *,
        lease_owner: str,
        lease_token: str | None = None,
        jury_path: str | Path,
        ui_result_path: str | Path | None = None,
        verdict: str,
        n_violations: int,
        next_stage: str | None = None,
        degradations: list[Any] | None = None,
        now: float | None = None,
    ) -> bool:
        if (
            verdict not in ("violation", "no_violation")
            or n_violations < 0
            or (verdict == "violation") != (n_violations > 0)
        ):
            raise ValueError("verdict and violations disagree")
        jury, ui = self._paths(jury_path, ui_result_path)
        now = time.time() if now is None else now
        if next_stage not in (None, "vlm"):
            raise ValueError("invalid next stage")
        return (
            self.db.connect()
            .execute(
                """UPDATE jobs SET provisional_at=?,jury_path=?,ui_result_path=?,
            result_level='rules',verdict=?,n_violations=?,degradations_json=?,
            stage=COALESCE(?,stage),status=CASE WHEN ? IS NULL THEN status ELSE 'queued' END,
            lease_owner=CASE WHEN ? IS NULL THEN lease_owner ELSE NULL END,
            lease_pid=CASE WHEN ? IS NULL THEN lease_pid ELSE NULL END,
            lease_token=CASE WHEN ? IS NULL THEN lease_token ELSE NULL END
            WHERE id=? AND status='running' AND lease_owner=? AND lease_token=?""",
                (
                    now,
                    jury,
                    ui,
                    verdict,
                    n_violations,
                    _json(degradations or []),
                    next_stage,
                    next_stage,
                    next_stage,
                    next_stage,
                    next_stage,
                    job_id,
                    lease_owner,
                    lease_token,
                ),
            )
            .rowcount
            == 1
        )

    def finalize(
        self,
        job_id: str,
        *,
        verdict: str,
        n_violations: int,
        jury_path: str | Path,
        ui_result_path: str | Path | None = None,
        lease_owner: str | None = None,
        lease_token: str | None = None,
        result_level: str = "rules",
        degradations: list[Any] | None = None,
        now: float | None = None,
        _expected: JobRecord | None = None,
    ) -> bool:
        existing = self.get_job(job_id)
        if existing is None or existing.status in ("done", "failed"):
            return False
        if result_level not in ("full", "partial", "rules"):
            raise ValueError("invalid result level")
        if (
            verdict not in ("violation", "no_violation")
            or n_violations < 0
            or (verdict == "violation") != (n_violations > 0)
        ):
            raise ValueError("verdict and violations disagree")
        jury, ui = self._paths(jury_path, ui_result_path)
        now = time.time() if now is None else now
        snapshot_sql, snapshot_values = self._snapshot_where(_expected)
        with self.db.transaction() as connection:
            row = connection.execute(
                """UPDATE jobs SET status='done',stage_code='done',progress=1,
                finished_at=?,result_level=?,verdict=?,n_violations=?,jury_path=?,ui_result_path=?,
                lease_owner=NULL,lease_pid=NULL,lease_token=NULL,degradations_json=COALESCE(?,degradations_json)
                WHERE id=? AND status IN ('queued','running')
                AND (? IS NULL OR (lease_owner=? AND lease_token=?))"""
                + snapshot_sql
                + " RETURNING *",
                (
                    now,
                    result_level,
                    verdict,
                    n_violations,
                    jury,
                    ui,
                    _json(degradations) if degradations is not None else None,
                    job_id,
                    lease_owner,
                    lease_owner,
                    lease_token,
                )
                + snapshot_values,
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                """UPDATE jobs SET status='done',stage_code='done',progress=1,finished_at=?,
                result_level=?,verdict=?,n_violations=?,jury_path=?,ui_result_path=?,degradations_json=?
                WHERE linked_to=? AND status IN ('queued','running')""",
                (
                    now,
                    result_level,
                    verdict,
                    n_violations,
                    jury,
                    ui,
                    row["degradations_json"],
                    job_id,
                ),
            )
            if result_level == "full":
                connection.execute(
                    "INSERT OR REPLACE INTO result_cache VALUES(?,?,?,?)",
                    (
                        row["sha256"],
                        row["pipeline_version"],
                        row["config_hash"],
                        job_id,
                    ),
                )
        return True

    def fail(
        self,
        job_id: str,
        error_code: str,
        error_message: str,
        *,
        lease_owner: str | None = None,
        lease_token: str | None = None,
        now: float | None = None,
        _expected: JobRecord | None = None,
    ) -> bool:
        now = time.time() if now is None else now
        snapshot_sql, snapshot_values = self._snapshot_where(_expected)
        with self.db.transaction() as connection:
            changed = connection.execute(
                """UPDATE jobs SET status='failed',stage_code='failed',finished_at=?,
                error_code=?,error_msg=?,lease_owner=NULL,lease_pid=NULL,lease_token=NULL WHERE id=?
                AND status IN ('queued','running') AND (? IS NULL OR (lease_owner=? AND lease_token=?))"""
                + snapshot_sql,
                (
                    now,
                    error_code,
                    error_message,
                    job_id,
                    lease_owner,
                    lease_owner,
                    lease_token,
                )
                + snapshot_values,
            ).rowcount
            if changed:
                connection.execute(
                    """UPDATE jobs SET status='failed',stage_code='failed',finished_at=?,
                    error_code=?,error_msg=? WHERE linked_to=? AND status IN ('queued','running')""",
                    (now, error_code, error_message, job_id),
                )
        return changed == 1

    def finalize_overdue(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        rows = (
            self.db.connect()
            .execute(
                """SELECT * FROM jobs WHERE status IN ('queued','running')
            AND linked_to IS NULL AND finalize_at<=? AND provisional_at IS NOT NULL""",
                (now,),
            )
            .fetchall()
        )
        count = 0
        for row in rows:
            job = _job(row)
            if job is None:  # инвариант: строка только что прочитана/записана в транзакции
                raise RuntimeError("нарушен инвариант очереди: job is None")
            if (
                job.jury_path
                and Path(job.jury_path).is_file()
                and (not job.ui_result_path or Path(job.ui_result_path).is_file())
            ):
                count += self.finalize(
                    job.id,
                    verdict=job.verdict or "no_violation",
                    n_violations=job.n_violations,
                    jury_path=job.jury_path,
                    ui_result_path=job.ui_result_path,
                    result_level="rules",
                    degradations=job.degradations + ["deadline_finalize"],
                    now=now,
                    _expected=job,
                )
        # With no validated evidence, a deadline failure is honest. Never invent
        # a clean verdict to make the service appear to meet the deadline.
        overdue = (
            self.db.connect()
            .execute(
                """SELECT * FROM jobs WHERE status IN ('queued','running')
            AND linked_to IS NULL AND received_at+250<=?""",
                (now,),
            )
            .fetchall()
        )
        for row in overdue:
            job = _job(row)
            if job is None:  # инвариант: строка только что прочитана/записана в транзакции
                raise RuntimeError("нарушен инвариант очереди: job is None")
            # If a worker published a valid result between the two queries, it
            # gets finalized on the next tick rather than being failed here.
            if (
                job.jury_path
                and Path(job.jury_path).is_file()
                and (not job.ui_result_path or Path(job.ui_result_path).is_file())
            ):
                continue
            count += self.fail(
                row["id"],
                "DEADLINE_EXCEEDED",
                "Время обработки истекло до получения результата.",
                now=now,
                _expected=job,
            )
        return count

    def requeue_dead(
        self, now: float | None = None, pid_alive: Callable[[int], bool] | None = None
    ) -> int:
        now = time.time() if now is None else now
        check_pid = pid_alive or process_alive
        rows = (
            self.db.connect()
            .execute("SELECT * FROM jobs WHERE status='running' AND linked_to IS NULL")
            .fetchall()
        )
        changed = 0
        for row in rows:
            job = _job(row)
            if job is None:  # инвариант: строка только что прочитана/записана в транзакции
                raise RuntimeError("нарушен инвариант очереди: job is None")
            # A stale heartbeat alone is insufficient to safely reassign work:
            # the supervisor first verifies and kills the old process. Otherwise
            # two live processes could perform the same expensive inference.
            if job.lease_pid and check_pid(job.lease_pid):
                continue
            if (
                job.provisional_at is not None
                and job.jury_path
                and Path(job.jury_path).is_file()
                and (not job.ui_result_path or Path(job.ui_result_path).is_file())
            ):
                changed += self.finalize(
                    job.id,
                    verdict=job.verdict or "no_violation",
                    n_violations=job.n_violations,
                    jury_path=job.jury_path,
                    ui_result_path=job.ui_result_path,
                    lease_owner=job.lease_owner,
                    lease_token=job.lease_token,
                    result_level="rules",
                    degradations=job.degradations + ["vlm_crash"],
                    now=now,
                    _expected=job,
                )
            elif job.attempts_perception >= 2 or job.stage == "vlm":
                changed += self.fail(
                    job.id,
                    "PIPELINE_CRASH",
                    "Обработка прервана после повторной попытки.",
                    lease_owner=job.lease_owner,
                    lease_token=job.lease_token,
                    now=now,
                    _expected=job,
                )
            else:
                changed += (
                    self.db.connect()
                    .execute(
                        """UPDATE jobs SET status='queued',stage_code='queued',
                    lease_owner=NULL,lease_pid=NULL,lease_token=NULL,alive_at=NULL,progress_at=NULL,perception_level='fast'
                    WHERE id=? AND status='running' AND lease_owner=? AND lease_token=? AND alive_at IS ?""",
                        (job.id, job.lease_owner, job.lease_token, job.alive_at),
                    )
                    .rowcount
                )
        return changed

    def queue_depth(self, stage: str | None = None, source: str | None = None) -> int:
        return (
            self.db.connect()
            .execute(
                """SELECT COUNT(*) FROM jobs WHERE status='queued' AND linked_to IS NULL
            AND (? IS NULL OR stage=?) AND (? IS NULL OR source=?)""",
                (stage, stage, source, source),
            )
            .fetchone()[0]
        )

    def active_count(self, client_ip: str | None = None, source: str | None = None) -> int:
        return (
            self.db.connect()
            .execute(
                """SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')
            AND (? IS NULL OR client_ip=?) AND (? IS NULL OR source=?)""",
                (client_ip, client_ip, source, source),
            )
            .fetchone()[0]
        )

    def queue_position(self, job_id: str) -> int | None:
        job = self.resolve_job(job_id)
        if job is None or job.status != "queued":
            return None
        return (
            self.db.connect()
            .execute(
                """SELECT COUNT(*) FROM jobs WHERE stage=? AND status='queued'
            AND linked_to IS NULL AND (priority,received_at,id)<=(?,?,?)""",
                (job.stage, job.priority, job.received_at, job.id),
            )
            .fetchone()[0]
        )

    def register_worker(
        self,
        name: str,
        role: str,
        pid: int,
        state: str = "idle",
        now: float | None = None,
    ) -> None:
        now = time.time() if now is None else now
        self.db.connect().execute(
            """INSERT INTO workers(name,role,pid,state,started_at,alive_at,progress_at)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET role=excluded.role,pid=excluded.pid,
            state=excluded.state,started_at=excluded.started_at,alive_at=excluded.alive_at,
            progress_at=excluded.progress_at,job_id=NULL,rss_mb=0,mlx_active_mb=0,models_json='[]',quant=NULL""",
            (name, role, pid, state, now, now, now),
        )

    def worker_heartbeat(
        self,
        name: str,
        state: str | None = None,
        job_id: str | None = None,
        now: float | None = None,
    ) -> None:
        now = time.time() if now is None else now
        self.db.connect().execute(
            """UPDATE workers SET alive_at=?,state=COALESCE(?,state),
            job_id=CASE WHEN ? IS NULL THEN job_id ELSE ? END,
            progress_at=CASE WHEN ?='busy' AND state!='busy' THEN ? ELSE progress_at END WHERE name=?""",
            (now, state, state, job_id, state, now, name),
        )

    def workers(self) -> list[dict[str, Any]]:
        return [
            dict(row) for row in self.db.connect().execute("SELECT * FROM workers ORDER BY name")
        ]
