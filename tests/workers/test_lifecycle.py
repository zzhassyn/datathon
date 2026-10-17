import hashlib
import json
import os
import signal
import threading
import time
from pathlib import Path

import pytest

from cashguard.core.ids import new_job_id
from cashguard.core.settings import Settings
from cashguard.jobs.repo import JobRepository
from cashguard.workers.base import Worker
from cashguard.workers.perception_worker import PerceptionWorker
from cashguard.workers.supervisor import Supervisor


def create_job(settings, make_video, *, scenario="violation", received_at=None):
    settings.paths.ensure()
    job_id = new_job_id()
    path = make_video(settings.paths.input_path(job_id), verdict=scenario)
    return JobRepository(settings.db_path).create_job(
        job_id=job_id,
        input_path=path,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        duration_sec=2,
        received_at=received_at,
        deduplicate=False,
    )


def wait_for(supervisor, predicate, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        supervisor.tick()
        if predicate():
            return
        time.sleep(0.03)
    raise AssertionError("worker condition did not become true before timeout")


def test_worker_publishes_durable_jury_and_ui(tmp_path, make_video):
    settings = Settings(data_dir=tmp_path, stub_delay_sec=0)
    job = create_job(settings, make_video)
    worker = PerceptionWorker(settings)
    worker.run(once=True)
    repository = JobRepository(settings.db_path)
    done = repository.get_job(job.id)
    assert done.status == "done" and done.attempts_perception == 1
    result = json.loads(Path(done.jury_path).read_text())
    ui = json.loads(Path(done.ui_result_path).read_text())
    assert result["verdict"] == ui["verdict"] == "violation"
    assert "image_base64" not in json.dumps(ui)
    assert (Path(done.jury_path).parent / "evidence/01_event.jpg").is_file()
    assert repository.workers()[0]["state"] == "draining"
    assert JobRepository(settings.db_path).get_job(job.id).jury_path == done.jury_path
    # Repeated worker startup must not process a completed job again.
    PerceptionWorker(settings).run(once=True)
    assert repository.get_job(job.id).attempts_perception == 1


def test_expired_job_fails_without_fake_result(tmp_path, make_video):
    settings = Settings(data_dir=tmp_path, stub_delay_sec=0)
    job = create_job(settings, make_video, received_at=time.time() - 240)
    PerceptionWorker(settings).run(once=True)
    result = JobRepository(settings.db_path).get_job(job.id)
    assert result.status == "failed"
    assert result.error_code == "PIPELINE_CRASH"
    assert "истекло" in result.error_msg
    assert result.jury_path is None


def test_killed_worker_restarts_and_completes_same_job(tmp_path, make_video):
    settings = Settings(data_dir=tmp_path, stub_delay_sec=1)
    job = create_job(settings, make_video)
    repository = JobRepository(settings.db_path)
    supervisor = Supervisor(
        settings, start_api=False, restart_base=0.05, shutdown_timeout=2
    )
    try:
        wait_for(supervisor, lambda: repository.get_job(job.id).status == "running")
        original = supervisor.children[0].process
        original.kill()  # SIGKILL on POSIX: no finally block or cooperative cleanup.
        original.wait(timeout=5)
        wait_for(supervisor, lambda: repository.get_job(job.id).status == "done")
        finished = repository.get_job(job.id)
        assert finished.attempts_perception == 2
        assert finished.perception_level == "fast"
        assert (
            json.loads(Path(finished.jury_path).read_text())["verdict"] == "violation"
        )
        assert supervisor.children[0].process.pid != original.pid
    finally:
        supervisor.close()


def test_crashes_stop_after_two_attempts(tmp_path, make_video):
    settings = Settings(data_dir=tmp_path, stub_delay_sec=2)
    job = create_job(settings, make_video)
    repository = JobRepository(settings.db_path)
    supervisor = Supervisor(
        settings, start_api=False, restart_base=0.05, shutdown_timeout=2
    )
    try:
        for expected_attempt in (1, 2):
            wait_for(
                supervisor,
                lambda expected_attempt=expected_attempt: (
                    repository.get_job(job.id).status == "running"
                    and repository.get_job(job.id).attempts_perception
                    == expected_attempt
                ),
            )
            process = supervisor.children[0].process
            process.kill()
            process.wait(timeout=5)
        wait_for(supervisor, lambda: repository.get_job(job.id).status == "failed")
        assert repository.get_job(job.id).error_code == "PIPELINE_CRASH"
        assert repository.get_job(job.id).attempts_perception == 2
    finally:
        supervisor.close()


def test_alive_heartbeat_preserves_busy_job_and_does_not_fake_progress(
    tmp_path, make_video
):
    settings = Settings(data_dir=tmp_path, heartbeat_interval_sec=0.02)
    job = create_job(settings, make_video)
    repository = JobRepository(settings.db_path)
    entered, release = threading.Event(), threading.Event()

    class WaitingWorker(Worker):
        def process(self, current):
            entered.set()
            release.wait(timeout=3)
            self.repo.fail(
                current.id,
                "INTERNAL",
                "Тест завершён",
                lease_owner=self.name,
                lease_token=current.lease_token,
            )

    worker = WaitingWorker(settings, name="heartbeat-test")
    thread = threading.Thread(target=lambda: worker.run(once=True))
    thread.start()
    try:
        assert entered.wait(timeout=2)
        initial = repository.workers()[0]
        time.sleep(0.08)
        latest = repository.workers()[0]
        assert latest["state"] == "busy"
        assert latest["job_id"] == job.id
        assert latest["alive_at"] > initial["alive_at"]
        assert latest["progress_at"] == initial["progress_at"]
    finally:
        release.set()
        thread.join(timeout=3)
    assert not thread.is_alive()


@pytest.mark.skipif(not hasattr(signal, "SIGSTOP"), reason="SIGSTOP requires POSIX")
def test_supervisor_kills_stalled_worker_before_requeue(tmp_path, make_video):
    settings = Settings(data_dir=tmp_path, stub_delay_sec=1)
    job = create_job(settings, make_video)
    repository = JobRepository(settings.db_path)
    supervisor = Supervisor(
        settings, start_api=False, restart_base=0.05, shutdown_timeout=2
    )
    try:
        wait_for(supervisor, lambda: repository.get_job(job.id).status == "running")
        original = supervisor.children[0].process
        os.kill(original.pid, signal.SIGSTOP)
        # Simulate elapsed heartbeat time without a 20-second test sleep.
        repository.db.connect().execute(
            "UPDATE workers SET alive_at=? WHERE pid=?",
            (time.time() - 21, original.pid),
        )
        wait_for(supervisor, lambda: original.poll() is not None)
        wait_for(supervisor, lambda: repository.get_job(job.id).status == "done")
        assert repository.get_job(job.id).attempts_perception == 2
    finally:
        supervisor.close()
