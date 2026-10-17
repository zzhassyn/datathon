"""Measured EWMA costs. Stub timings must never be recorded as model timings."""

import math

from cashguard.jobs.db import Database


class PerfStats:
    def __init__(self, database: Database, alpha: float = 0.2) -> None:
        if not 0 < alpha <= 1:
            raise ValueError("alpha must be in (0,1]")
        self.db = database
        self.alpha = alpha

    def observe(self, key: str, seconds: float) -> float:
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("cost must be finite and nonnegative")
        row = (
            self.db.connect()
            .execute(
                """INSERT INTO perf_stats VALUES(?,?,1)
            ON CONFLICT(key) DO UPDATE SET ewma=?*excluded.ewma+(1-?)*perf_stats.ewma,n=perf_stats.n+1
            RETURNING ewma""",
                (key, seconds, self.alpha, self.alpha),
            )
            .fetchone()
        )
        return row[0]

    def get(self, key: str, default: float | None = None) -> float | None:
        row = (
            self.db.connect()
            .execute("SELECT ewma FROM perf_stats WHERE key=?", (key,))
            .fetchone()
        )
        return row[0] if row else default
