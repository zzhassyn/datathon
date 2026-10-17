"""Legacy stub result fragment. Typed model findings use render.analysis instead.

API views add lifecycle metadata and validate the assembled UiJobView.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from cashguard.pipeline.render.jury import RenderError, validate_jury

_FRAME_NAME = re.compile(r"\d{1,2}_(event|zoom|context)\.jpg\Z")
_JOB_ID = re.compile(r"[a-f0-9]{32}\Z")


def build_ui_result_fragment(
    job_id: str,
    jury_result: Mapping[str, Any],
    *,
    evidence_names: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """Return only result text and frame URLs, without any base64 or metadata."""
    if not _JOB_ID.fullmatch(job_id) or jury_result.get("job_id") != job_id:
        raise RenderError("Неверный job_id UI результата")
    validate_jury(jury_result)
    violations = jury_result["violations"]
    if len(evidence_names) != len(violations):
        raise RenderError("Имена evidence не совпадают с результатом")
    output = []
    for violation, names in zip(violations, evidence_names, strict=True):
        frames = violation["evidence_frames"]
        if len(names) != len(frames):
            raise RenderError("Имена evidence не совпадают с результатом")
        evidence = []
        for frame, name in zip(frames, names, strict=True):
            if not _FRAME_NAME.fullmatch(name):
                raise RenderError("Небезопасное имя evidence")
            evidence.append(
                {
                    "timestamp_sec": frame["timestamp_sec"],
                    "url": f"/api/ui/jobs/{job_id}/frames/{name}",
                }
            )
        output.append(
            {
                "title_ru": violation["type"],
                "timecode_sec": violation["timecode_sec"],
                "description_ru": violation["description"],
                "evidence": evidence,
            }
        )
    return {
        "verdict": jury_result["verdict"],
        "violations": output,
        "signals": [],
        "provisional": False,
        "degradations": [],
    }
