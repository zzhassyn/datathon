import sqlite3
from pathlib import Path

import pytest

from cashguard.core.atomic import atomic_write_json
from cashguard.core.paths import RuntimePaths
from cashguard.jobs.cache import ResultCache
from cashguard.jobs.db import _SCHEMA, SCHEMA_VERSION, Database
from cashguard.jobs.perfstats import PerfStats
from cashguard.jobs.repo import JobRepository
from cashguard.jobs.retention import purge_expired


@pytest.fixture
def repo(tmp_path):
    return JobRepository(tmp_path / "db.sqlite3")


def create(repo, **kwargs):
    options = {
        "input_path": "synthetic.mov",
        "sha256": "a" * 64,
        "received_at": 100,
        "duration_sec": 2,
    }
    options.update(kwargs)
    return repo.create_job(**options)


def publish(tmp_path, job):
    return atomic_write_json(
        tmp_path / job.id / "jury.json",
        {
            "job_id": job.id,
            "status": "done",
            "verdict": "no_violation",
            "violations": [],
        },
    )


def test_migration_pragmas_restart(repo):
    connection = repo.db.connect()
    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connection.execute("PRAGMA synchronous").fetchone()[0] == 1
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    job = create(repo)
    repo.db.close()
    reopened = JobRepository(repo.db.path)
    assert reopened.get_job(job.id) == job
    reopened.db.initialize()
    assert reopened.get_job(job.id) == job


def test_claim_ownership_and_idempotent_finalization(repo, tmp_path):
    job = create(repo)
    claimed = repo.claim_next("worker-one", pid=123, now=101)
    assert claimed.id == job.id
    assert claimed.attempts_perception == 1
    assert claimed.public_status == "processing"
    assert repo.claim_next("worker-two", now=102) is None
    result = publish(tmp_path, job)
    assert not repo.finalize(
        job.id,
        lease_owner="worker-two",
        lease_token=claimed.lease_token,
        jury_path=result,
        verdict="no_violation",
        n_violations=0,
    )
    assert repo.finalize(
        job.id,
        lease_owner="worker-one",
        lease_token=claimed.lease_token,
        jury_path=result,
        verdict="no_violation",
        n_violations=0,
        now=110,
    )
    before = repo.get_job(job.id)
    assert not repo.finalize(
        job.id, jury_path=Path("missing"), verdict="violation", n_violations=1, now=120
    )
    assert not repo.fail(job.id, "ERROR", "late failure")
    assert not repo.beat_progress(job.id, "worker-one", now=200)
    assert repo.get_job(job.id) == before


def test_finalization_requires_published_files_and_valid_verdict(repo, tmp_path):
    job = create(repo)
    with pytest.raises(ValueError):
        repo.finalize(
            job.id,
            jury_path=tmp_path / "missing",
            verdict="no_violation",
            n_violations=0,
        )
    assert repo.get_job(job.id).status == "queued"
    with pytest.raises(ValueError):
        repo.finalize(
            job.id,
            jury_path=publish(tmp_path, job),
            verdict="violation",
            n_violations=0,
        )


def test_dedupe_versioned_active_and_full_only(repo, tmp_path):
    first = create(repo)
    linked = create(repo)
    distinct_version = create(repo, pipeline_version="different")
    assert linked.id != first.id
    assert linked.linked_to == first.id
    assert distinct_version.linked_to is None
    assert repo.resolve_job(linked.id).id == first.id
    assert repo.queue_depth() == 2
    repo.finalize(
        first.id,
        jury_path=publish(tmp_path, first),
        verdict="no_violation",
        n_violations=0,
        result_level="rules",
    )
    assert repo.get_job(linked.id).status == "done"
    assert ResultCache(repo.db).get(first.sha256, first.pipeline_version, first.config_hash) is None
    second = create(repo)
    assert second.linked_to is None
    repo.finalize(
        second.id,
        jury_path=publish(tmp_path, second),
        verdict="no_violation",
        n_violations=0,
        result_level="full",
    )
    assert (
        ResultCache(repo.db).get(second.sha256, second.pipeline_version, second.config_hash).id
        == second.id
    )
    cached = create(repo)
    assert cached.status == "done"
    assert cached.linked_to == second.id


def test_recovery_attempts_heartbeat_and_stale_owner(repo):
    job = create(repo)
    repo.claim_next("dead", pid=123, now=101)
    assert repo.requeue_dead(now=102, pid_alive=lambda _: True) == 0
    assert repo.requeue_dead(now=102, pid_alive=lambda _: False) == 1
    second = repo.claim_next("alive", pid=124, now=103)
    assert second.attempts_perception == 2
    assert second.perception_level == "fast"
    assert repo.beat_alive("dead", now=104) == 0
    assert not repo.fail(job.id, "ERROR", "stale", lease_owner="dead")
    assert repo.requeue_dead(now=124, pid_alive=lambda _: True) == 0
    assert repo.claim_next("third") is None
    assert repo.requeue_dead(now=124, pid_alive=lambda _: False) == 1
    assert repo.get_job(job.id).status == "failed"
    assert repo.get_job(job.id).error_code == "PIPELINE_CRASH"
    assert repo.claim_next("third") is None


def test_no_provisional_result_expires_without_false_clean_verdict(repo):
    job = create(repo)
    assert repo.finalize_overdue(now=349) == 0
    assert repo.finalize_overdue(now=350) == 1
    record = repo.get_job(job.id)
    assert record.status == "failed"
    assert record.error_code == "DEADLINE_EXCEEDED"
    assert record.verdict is None
    assert record.jury_path is None


def test_missing_provisional_artifact_cannot_leave_job_processing(repo, tmp_path):
    job = create(repo)
    repo.claim_next("worker", now=101)
    result = publish(tmp_path, job)
    repo.set_provisional(
        job.id,
        lease_owner="worker",
        lease_token=repo.get_job(job.id).lease_token,
        jury_path=result,
        verdict="no_violation",
        n_violations=0,
        now=110,
    )
    result.unlink()
    assert repo.finalize_overdue(now=350) == 1
    assert repo.get_job(job.id).status == "failed"


def test_provisional_deadline_and_vlm_crash_finalize(repo, tmp_path):
    job = create(repo)
    repo.claim_next("worker", now=101)
    assert repo.set_provisional(
        job.id,
        lease_owner="worker",
        lease_token=repo.get_job(job.id).lease_token,
        jury_path=publish(tmp_path, job),
        verdict="no_violation",
        n_violations=0,
        next_stage="vlm",
        now=110,
    )
    assert repo.finalize_overdue(now=324) == 0
    assert repo.finalize_overdue(now=325) == 1
    assert repo.finalize_overdue(now=326) == 0
    assert repo.get_job(job.id).degradations == ["deadline_finalize"]
    other = create(repo, sha256="b" * 64)
    repo.claim_next("worker", now=101)
    repo.set_provisional(
        other.id,
        lease_owner="worker",
        lease_token=repo.get_job(other.id).lease_token,
        jury_path=publish(tmp_path, other),
        verdict="no_violation",
        n_violations=0,
        next_stage="vlm",
        now=110,
    )
    repo.claim_next("vlm", stage="vlm", now=120)
    assert repo.requeue_dead(now=121, pid_alive=lambda _: False) == 1
    assert repo.get_job(other.id).status == "done"
    assert "vlm_crash" in repo.get_job(other.id).degradations


def test_progress_throttled_and_worker_heartbeat_does_not_hide_stall(repo):
    job = create(repo)
    repo.register_worker("worker", "all", 123, now=100)
    repo.claim_next("worker", now=101)
    repo.worker_heartbeat("worker", state="busy", job_id=job.id, now=101)
    token = repo.get_job(job.id).lease_token
    assert not repo.report_progress(job.id, "worker", "decode", 0.1, now=101.2, lease_token=token)
    assert repo.report_progress(job.id, "worker", "decode", 0.2, now=101.5, lease_token=token)
    assert not repo.report_progress(job.id, "worker", "decode", 0.3, now=101.9, lease_token=token)
    repo.worker_heartbeat("worker", now=110)
    worker = repo.workers()[0]
    assert worker["alive_at"] == 110
    assert worker["progress_at"] == 101.5
    assert worker["job_id"] == job.id
    assert repo.db.connect().execute("SELECT COUNT(*) FROM job_events").fetchone()[0] == 1


def test_priority_queue_and_ip_active_counts(repo):
    ui = create(repo, source="ui", client_ip="127.0.0.1", sha256="ui")
    jury = create(repo, sha256="jury", received_at=101)
    assert repo.queue_position(jury.id) == 1
    assert repo.queue_position(ui.id) == 2
    assert repo.active_count(source="ui", client_ip="127.0.0.1") == 1
    assert repo.claim_next("worker", now=102).id == jury.id


def test_retention_preserves_live_jobs_and_parents_of_recent_links(repo, tmp_path):
    paths = RuntimePaths(tmp_path / "runtime")
    paths.ensure()
    first = create(repo)
    result = atomic_write_json(paths.job_dir(first.id) / "jury.json", {})
    repo.finalize(
        first.id,
        jury_path=result,
        verdict="no_violation",
        n_violations=0,
        result_level="full",
        now=110,
    )
    linked = create(repo, received_at=4000)
    active = create(repo, sha256="active")
    assert purge_expired(repo, paths, retention_hours=0, now=8000) == []
    assert purge_expired(repo, paths, retention_hours=1, now=5000) == []
    assert repo.get_job(first.id) is not None
    removed = purge_expired(repo, paths, retention_hours=1, now=8000)
    assert set(removed) == {first.id, linked.id}
    assert repo.get_job(active.id) is not None
    assert not result.exists()


def test_ewma(repo):
    stats = PerfStats(repo.db, alpha=0.5)
    assert stats.get("missing") is None
    assert stats.observe("stub:perception", 10) == 10
    assert stats.observe("stub:perception", 20) == 15
    assert stats.get("stub:perception") == 15


def test_v1_database_migrates_without_losing_jobs(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    for statement in _SCHEMA:
        connection.execute(statement)
    connection.execute("PRAGMA user_version=1")
    connection.execute(
        """INSERT INTO jobs(id,source,status,received_at,vision_target,vlm_start_cutoff,
        vlm_stop,finalize_at,perception_hard_stop,input_path,sha256,pipeline_version,config_hash,lease_owner,lease_pid)
        VALUES(?, 'jury','running',100,250,280,310,325,335,
               'synthetic.mov','sha','v1','hash','worker',123)""",
        ("a" * 32,),
    )
    connection.commit()
    connection.close()
    repo = JobRepository(Database(path))
    job = repo.get_job("a" * 32)
    assert job.lease_token and len(job.lease_token) == 32
    assert job.status == "running"
    assert repo.requeue_dead(now=110, pid_alive=lambda _: False) == 1
    assert repo.get_job(job.id).status == "queued"


def test_retry_with_same_worker_name_rejects_every_stale_mutation(repo, tmp_path):
    job = create(repo)
    first = repo.claim_next("worker", pid=123, now=101)
    repo.requeue_dead(now=102, pid_alive=lambda _: False)
    second = repo.claim_next("worker", pid=124, now=103)
    assert second.lease_token != first.lease_token
    result = publish(tmp_path, job)
    kwargs = {"lease_owner": "worker", "lease_token": first.lease_token}
    assert not repo.finalize(
        job.id, jury_path=result, verdict="no_violation", n_violations=0, **kwargs
    )
    assert not repo.fail(job.id, "STALE", "stale attempt", **kwargs)
    assert not repo.set_provisional(
        job.id, jury_path=result, verdict="no_violation", n_violations=0, **kwargs
    )
    assert not repo.beat_progress(job.id, "worker", now=110, lease_token=first.lease_token)
    assert not repo.report_progress(
        job.id, "worker", "stale", 1, now=110, lease_token=first.lease_token
    )
    assert repo.beat_alive("worker", now=110, lease_token=first.lease_token) == 0
    assert repo.get_job(job.id) == second
    assert repo.finalize(
        job.id,
        jury_path=result,
        verdict="no_violation",
        n_violations=0,
        lease_owner="worker",
        lease_token=second.lease_token,
    )


def test_stale_recovery_snapshot_cannot_requeue_new_attempt(repo):
    job = create(repo)
    repo.claim_next("same-name", pid=123, now=101)
    other = JobRepository(repo.db.path)

    def recover_in_parallel(_):
        assert other.requeue_dead(now=101, pid_alive=lambda _: False) == 1
        other.claim_next("same-name", pid=124, now=101)
        return False

    assert repo.requeue_dead(now=101, pid_alive=recover_in_parallel) == 0
    current = repo.get_job(job.id)
    assert current.status == "running"
    assert current.lease_pid == 124
    assert current.attempts_perception == 2


def test_sweeper_snapshot_cannot_finalize_an_obsolete_provisional(repo, tmp_path, monkeypatch):
    job = create(repo)
    claim = repo.claim_next("worker", now=101)
    first = publish(tmp_path, job)
    second = atomic_write_json(tmp_path / "new-jury.json", {})
    credentials = {"lease_owner": "worker", "lease_token": claim.lease_token}
    repo.set_provisional(
        job.id,
        jury_path=first,
        verdict="no_violation",
        n_violations=0,
        now=110,
        **credentials,
    )
    original = repo.finalize

    def publish_before_finalization(*args, **kwargs):
        repo.set_provisional(
            job.id,
            jury_path=second,
            verdict="violation",
            n_violations=1,
            now=330,
            **credentials,
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(repo, "finalize", publish_before_finalization)
    assert repo.finalize_overdue(now=350) == 0
    assert repo.get_job(job.id).status == "running"
    monkeypatch.setattr(repo, "finalize", original)
    assert repo.finalize_overdue(now=350) == 1
    assert repo.get_job(job.id).jury_path == str(second)
    assert repo.get_job(job.id).verdict == "violation"


def test_sweeper_cannot_fail_a_concurrently_published_provisional(repo, tmp_path, monkeypatch):
    job = create(repo)
    claim = repo.claim_next("worker", now=101)
    original = repo.fail

    def publish_before_failure(*args, **kwargs):
        repo.set_provisional(
            job.id,
            jury_path=publish(tmp_path, job),
            verdict="no_violation",
            n_violations=0,
            lease_owner="worker",
            lease_token=claim.lease_token,
            now=350,
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(repo, "fail", publish_before_failure)
    assert repo.finalize_overdue(now=350) == 0
    assert repo.finalize_overdue(now=351) == 1
    assert repo.get_job(job.id).status == "done"


def test_jury_duplicate_promotes_queued_ui_parent(repo):
    parent = create(repo, source="ui")
    unrelated = create(repo, sha256="other", received_at=101)
    linked = create(repo, source="jury", received_at=102)
    assert linked.linked_to == parent.id
    assert repo.get_job(parent.id).priority == 0
    assert repo.claim_next("worker", now=103).id == parent.id
    assert repo.queue_position(unrelated.id) == 1


def test_retention_retries_failed_disk_cleanup_without_dangling_rows(repo, tmp_path, monkeypatch):
    from cashguard.jobs import retention

    paths = RuntimePaths(tmp_path / "runtime")
    paths.ensure()
    job = create(repo)
    result = atomic_write_json(paths.job_dir(job.id) / "jury.json", {})
    repo.finalize(job.id, jury_path=result, verdict="no_violation", n_violations=0, now=110)
    original = retention.shutil.rmtree

    def disk_error(*args, **kwargs):
        raise PermissionError("simulated temporary deletion error")

    monkeypatch.setattr(retention.shutil, "rmtree", disk_error)
    with pytest.raises(PermissionError):
        purge_expired(repo, paths, retention_hours=1, now=8000)
    assert repo.get_job(job.id) is None
    monkeypatch.setattr(retention.shutil, "rmtree", original)
    purge_expired(repo, paths, retention_hours=1, now=8000)
    assert list(paths.jobs_dir.iterdir()) == []
