import multiprocessing

from cashguard.core.atomic import atomic_write_json
from cashguard.jobs.repo import JobRepository


def claim_process(path, owner, barrier, output):
    repo = JobRepository(path)
    barrier.wait(timeout=10)
    job = repo.claim_next(owner)
    output.put(job.id if job else None)
    repo.db.close()


def finalize_process(path, job_id, result, barrier, output):
    repo = JobRepository(path)
    barrier.wait(timeout=10)
    output.put(
        repo.finalize(job_id, jury_path=result, verdict="no_violation", n_violations=0)
    )
    repo.db.close()


def run_race(target, args):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    output = context.Queue()
    processes = [
        context.Process(target=target, args=(*arguments, barrier, output))
        for arguments in args
    ]
    for process in processes:
        process.start()
    results = [output.get(timeout=15), output.get(timeout=15)]
    for process in processes:
        process.join(timeout=15)
        if process.is_alive():
            process.kill()
            process.join()
        assert process.exitcode == 0
    output.close()
    return results


def test_two_process_claim_only_one_owner(tmp_path):
    database = tmp_path / "jobs.sqlite3"
    repo = JobRepository(database)
    job = repo.create_job(input_path="synthetic.mov", sha256="synthetic")
    results = run_race(claim_process, [(database, "one"), (database, "two")])
    assert results.count(job.id) == 1
    assert results.count(None) == 1
    assert repo.get_job(job.id).attempts_perception == 1


def test_two_process_finalize_only_one_transition(tmp_path):
    database = tmp_path / "jobs.sqlite3"
    repo = JobRepository(database)
    job = repo.create_job(input_path="synthetic.mov", sha256="synthetic")
    result = atomic_write_json(tmp_path / "jury.json", {})
    results = run_race(finalize_process, [(database, job.id, result)] * 2)
    assert sorted(results) == [False, True]
    assert repo.get_job(job.id).status == "done"
