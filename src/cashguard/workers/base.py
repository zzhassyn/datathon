"""Single-job worker with independent liveness and progress heartbeats."""

import os
import signal
import threading
from pathlib import Path

import psutil
import structlog

from cashguard.core.settings import Settings
from cashguard.jobs.repo import JobRecord, JobRepository
from cashguard.workers.warmup import warmup


class Worker:
    def __init__(self, settings: Settings, *, name: str | None = None, role: str = "all") -> None:
        if role not in {"all", "perception"}:
            raise ValueError("VLM worker принадлежит S6 и ещё не подключён")
        self.settings = settings
        self.role = role
        self.name = name or f"{role}-{os.getpid()}"
        self.repo = JobRepository(settings.db_path, deadlines_path=Path("configs/deadlines.yaml"))
        self.stop_event = threading.Event()
        self.heartbeat_stop = threading.Event()
        self.completed = 0
        self.current_job: JobRecord | None = None

    def stop(self) -> None:
        # Finish the current job; supervisor imposes the hard shutdown bound.
        self.stop_event.set()

    def _heartbeat(self) -> None:
        try:
            while not self.heartbeat_stop.is_set():
                self.repo.worker_heartbeat(self.name)
                current = self.current_job
                if current is not None:
                    self.repo.beat_alive(self.name, lease_token=current.lease_token)
                self.heartbeat_stop.wait(self.settings.heartbeat_interval_sec)
        finally:
            self.repo.db.close()

    def process(self, job: JobRecord) -> None:
        raise NotImplementedError

    def run_once(self) -> bool:
        self.repo.finalize_overdue()
        self.repo.requeue_dead()
        job = self.repo.claim_next(self.name, pid=os.getpid())
        if job is None:
            return False
        self.current_job = job
        structlog.get_logger().info(
            "job_claimed",
            job_id=job.id,
            worker=self.name,
            attempt=job.attempts_perception,
            mode=self.settings.pipeline_mode,
        )
        self.repo.worker_heartbeat(self.name, state="busy", job_id=job.id)
        try:
            self.process(job)
        finally:
            self.current_job = None
            self.repo.worker_heartbeat(self.name, state="idle")
        self.completed += 1
        return True

    def run(self, *, once: bool = False) -> None:
        self.repo.register_worker(self.name, self.role, os.getpid(), state="warming")
        heartbeat = threading.Thread(target=self._heartbeat, name="worker-heartbeat", daemon=True)
        heartbeat.start()
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, lambda *_: self.stop())
            signal.signal(signal.SIGINT, lambda *_: self.stop())
        try:
            warmup(self.settings.pipeline_mode)
            self.repo.worker_heartbeat(self.name, state="idle")
            self.repo.requeue_dead()
            while not self.stop_event.is_set():
                did_work = self.run_once()
                if once or self.completed >= self.settings.max_worker_jobs:
                    break
                if psutil.Process().memory_info().rss > 3 * 1024**3:
                    break
                if not did_work:
                    self.stop_event.wait(self.settings.worker_poll_sec)
        finally:
            self.heartbeat_stop.set()
            heartbeat.join(timeout=self.settings.heartbeat_interval_sec + 1)
            self.repo.worker_heartbeat(self.name, state="draining")
            self.repo.db.close()
