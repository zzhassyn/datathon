"""Opaque identifiers; client filenames never become paths."""

import re
from uuid import uuid4

_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


def new_job_id() -> str:
    return uuid4().hex


def valid_job_id(value: str) -> bool:
    return bool(_JOB_ID.fullmatch(value))


def validate_job_id(value: str) -> str:
    if not valid_job_id(value):
        raise ValueError("invalid job id")
    return value
