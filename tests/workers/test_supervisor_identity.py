"""Windows venv redirectors have a different PID from the actual worker."""

from unittest.mock import Mock

import pytest

from cashguard.core.settings import Settings
from cashguard.workers.supervisor import Child, Supervisor


def setup_redirector(tmp_path, monkeypatch):
    supervisor = Supervisor(Settings(data_dir=tmp_path), start_api=False)
    popen = Mock(pid=100)
    popen.poll.return_value = None
    child = Child(
        "all-test",
        ["python", "-m", "cashguard.workers", "--role", "all", "--name", "all-test"],
        process=popen,
        started_at=600,
    )
    redirector = Mock(pid=100)
    worker = Mock(pid=200)
    redirector.children.return_value = [worker]
    redirector.cmdline.return_value = child.command
    worker.cmdline.return_value = child.command
    monkeypatch.setattr("cashguard.workers.supervisor.psutil.Process", lambda pid: redirector)
    return supervisor, child, redirector, worker, popen


@pytest.mark.parametrize("state", ["idle", "busy", "warming"])
def test_fresh_descendant_worker_is_not_killed_after_redirector_grace(tmp_path, monkeypatch, state):
    supervisor, child, redirector, worker, popen = setup_redirector(tmp_path, monkeypatch)
    supervisor._kill_stalled(
        child,
        {child.name: {"pid": worker.pid, "state": state, "alive_at": 999, "progress_at": 999}},
        1000,
    )
    popen.kill.assert_not_called()
    redirector.kill.assert_not_called()
    worker.kill.assert_not_called()


def test_stalled_descendant_worker_is_killed_with_its_redirector(tmp_path, monkeypatch):
    supervisor, child, redirector, worker, _ = setup_redirector(tmp_path, monkeypatch)
    supervisor._kill_stalled(
        child,
        {child.name: {"pid": worker.pid, "state": "busy", "alive_at": 999, "progress_at": 939}},
        1000,
    )
    worker.kill.assert_called_once()
    redirector.kill.assert_called_once()


def test_foreign_registered_pid_cannot_supply_heartbeat_for_this_worker(tmp_path, monkeypatch):
    supervisor, child, redirector, worker, _ = setup_redirector(tmp_path, monkeypatch)
    supervisor._kill_stalled(
        child,
        {child.name: {"pid": 300, "state": "idle", "alive_at": 999, "progress_at": 999}},
        1000,
    )
    worker.kill.assert_called_once()
    redirector.kill.assert_called_once()
