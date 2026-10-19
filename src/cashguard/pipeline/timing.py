"""PTS comparisons: tolerate floating-point rounding, never a missing sample."""

import math


def at_least(value: float, limit: float) -> bool:
    return value >= limit or math.isclose(value, limit, rel_tol=0, abs_tol=1e-9)


def at_most(value: float, limit: float) -> bool:
    return value <= limit or math.isclose(value, limit, rel_tol=0, abs_tol=1e-9)
