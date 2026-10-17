"""Short explicit sqlite3 transactions; connections are thread and process local."""

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 2

_SCHEMA = [
    """CREATE TABLE jobs (
      id TEXT PRIMARY KEY, source TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0,
      client_ip TEXT, status TEXT NOT NULL CHECK(status IN ('queued','running','done','failed')),
      stage TEXT NOT NULL DEFAULT 'perception', perception_level TEXT NOT NULL DEFAULT 'full',
      result_level TEXT, progress REAL NOT NULL DEFAULT 0, stage_code TEXT NOT NULL DEFAULT 'queued',
      stages_json TEXT NOT NULL DEFAULT '[]', received_at REAL NOT NULL,
      vision_target REAL NOT NULL, vlm_start_cutoff REAL NOT NULL, vlm_stop REAL NOT NULL,
      finalize_at REAL NOT NULL, perception_hard_stop REAL NOT NULL,
      started_at REAL, provisional_at REAL, finished_at REAL,
      attempts_perception INTEGER NOT NULL DEFAULT 0, attempts_vlm INTEGER NOT NULL DEFAULT 0,
      lease_owner TEXT, lease_pid INTEGER, alive_at REAL, progress_at REAL,
      input_path TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL DEFAULT 0,
      duration_sec REAL NOT NULL DEFAULT 0, width INTEGER NOT NULL DEFAULT 0,
      height INTEGER NOT NULL DEFAULT 0, rotation INTEGER NOT NULL DEFAULT 0,
      codec TEXT NOT NULL DEFAULT '', needs_transcode INTEGER NOT NULL DEFAULT 0,
      verdict TEXT, n_violations INTEGER NOT NULL DEFAULT 0,
      linked_to TEXT REFERENCES jobs(id) ON DELETE RESTRICT,
      error_code TEXT, error_msg TEXT, timings_json TEXT NOT NULL DEFAULT '{}',
      degradations_json TEXT NOT NULL DEFAULT '[]', metadata_json TEXT NOT NULL DEFAULT '{}',
      pipeline_version TEXT NOT NULL, config_hash TEXT NOT NULL,
      jury_path TEXT, ui_result_path TEXT)""",
    "CREATE INDEX jobs_claim ON jobs(stage,status,priority,received_at,id)",
    "CREATE INDEX jobs_sha256 ON jobs(sha256,pipeline_version,config_hash)",
    "CREATE INDEX jobs_deadline ON jobs(status,finalize_at)",
    "CREATE INDEX jobs_linked ON jobs(linked_to)",
    """CREATE TABLE job_events(id INTEGER PRIMARY KEY AUTOINCREMENT,
       job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
       at REAL NOT NULL, kind TEXT NOT NULL, data_json TEXT NOT NULL)""",
    "CREATE INDEX events_job_time ON job_events(job_id,at)",
    """CREATE TABLE workers(name TEXT PRIMARY KEY, role TEXT NOT NULL, pid INTEGER NOT NULL,
       state TEXT NOT NULL, started_at REAL NOT NULL, alive_at REAL NOT NULL, progress_at REAL,
       job_id TEXT, rss_mb REAL NOT NULL DEFAULT 0, mlx_active_mb REAL NOT NULL DEFAULT 0,
       models_json TEXT NOT NULL DEFAULT '[]', quant TEXT)""",
    """CREATE TABLE result_cache(sha256 TEXT NOT NULL, pipeline_version TEXT NOT NULL,
       config_hash TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
       PRIMARY KEY(sha256,pipeline_version,config_hash))""",
    "CREATE TABLE perf_stats(key TEXT PRIMARY KEY, ewma REAL NOT NULL, n INTEGER NOT NULL)",
]


class Database:
    def __init__(self, path: str | Path) -> None:
        if sqlite3.sqlite_version_info < (3, 35, 0):
            raise RuntimeError("SQLite >= 3.35 is required for RETURNING")
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._local = threading.local()
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        if getattr(self._local, "pid", None) != os.getpid():
            self._local.connection = None
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(self.path, isolation_level=None, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=5000")
            if connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                connection.close()
                raise RuntimeError("SQLite WAL mode is required")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA foreign_keys=ON")
            self._local.connection = connection
            self._local.pid = os.getpid()
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()

    def initialize(self) -> None:
        with self.transaction() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("database schema is newer than this application")
            if version == 0:
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version=1")
                version = 1
            if version == 1:
                connection.execute("ALTER TABLE jobs ADD COLUMN lease_token TEXT")
                connection.execute(
                    "UPDATE jobs SET lease_token=lower(hex(randomblob(16))) WHERE status='running'"
                )
                connection.execute("PRAGMA user_version=2")

    def close(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None
