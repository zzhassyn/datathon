"""Frozen jury rendering; temporary mappings until S0 publishes Python contracts.

This module never manufactures evidence or converts an unrenderable positive
finding into a clean result. Callers supply JPEG bytes from the evidence stage.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
from collections.abc import Callable, Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class RenderError(ValueError):
    """The response cannot be safely published to the jury API."""


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    path = Path(__file__).resolve().parents[2] / "contracts/schemas/jury_job.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RenderError("Таймкод должен быть конечным числом")
    value = float(value)
    if not math.isfinite(value):
        raise RenderError("Таймкод должен быть конечным числом")
    return value


def _duration(value: float | None) -> float | None:
    if value is None:
        return None
    value = _number(value)
    if value < 0:
        raise RenderError("Длительность не может быть отрицательной")
    return value


def _time(value: Any, duration_sec: float | None, safe: bool) -> float:
    try:
        value = _number(value)
    except RenderError:
        if not safe:
            raise
        value = 0.0
    if not safe and (value < 0 or (duration_sec is not None and value > duration_sec)):
        raise RenderError("Таймкод вне длительности видео")
    value = max(0.0, value)
    if duration_sec is not None:
        value = min(value, duration_sec)
    rounded = round(value, 2)
    # A rounded timestamp must not exceed the actual duration, even if the
    # duration is not itself an integer number of hundredths.
    if duration_sec is not None and rounded > duration_sec:
        rounded = math.floor(duration_sec * 100) / 100
    return float(rounded)


def validate_jpeg(raw: bytes) -> None:
    """Check JPEG marker framing without importing an image/ML library.

    The evidence producer must decode/encode pixels. This guard rejects missing
    SOF/SOS/EOI, truncated segments, zero dimensions and invalid marker layouts;
    it is deliberately not advertised as an entropy decoder.
    """
    if not isinstance(raw, bytes) or not raw.startswith(b"\xff\xd8\xff"):
        raise RenderError("Evidence должен быть JPEG")
    if not raw.endswith(b"\xff\xd9"):
        raise RenderError("JPEG оборван")
    offset = 2
    seen_sof = seen_sos = False
    in_scan = False
    while offset < len(raw):
        if in_scan:
            marker_at = raw.find(b"\xff", offset)
            if marker_at < 0:
                raise RenderError("JPEG не завершён")
            offset = marker_at
        if raw[offset] != 0xFF:
            raise RenderError("Неверный маркер JPEG")
        while offset < len(raw) and raw[offset] == 0xFF:
            offset += 1
        if offset >= len(raw):
            raise RenderError("JPEG оборван")
        marker = raw[offset]
        offset += 1
        if in_scan and (marker == 0 or 0xD0 <= marker <= 0xD7):
            continue
        in_scan = False
        if marker == 0xD9:
            if offset != len(raw) or not seen_sof or not seen_sos:
                raise RenderError("JPEG не содержит изображения")
            return
        if marker in (0, 0xD8) or 0xD0 <= marker <= 0xD7:
            raise RenderError("Неверный маркер JPEG")
        if marker == 0x01:
            continue
        if offset + 2 > len(raw):
            raise RenderError("JPEG оборван")
        length = int.from_bytes(raw[offset : offset + 2], "big")
        end = offset + length
        if length < 2 or end > len(raw):
            raise RenderError("JPEG содержит оборванный сегмент")
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            if (
                length < 8
                or not int.from_bytes(raw[offset + 3 : offset + 5], "big")
                or not int.from_bytes(raw[offset + 5 : offset + 7], "big")
            ):
                raise RenderError("JPEG имеет неверные размеры")
            seen_sof = True
        if marker == 0xDA:
            if not seen_sof or length < 6:
                raise RenderError("JPEG имеет неверный scan header")
            seen_sos = in_scan = True
        offset = end
    raise RenderError("JPEG не завершён")


def _jpeg(frame: Mapping[str, Any], reencode: Callable[[bytes], bytes] | None) -> str:
    raw = frame.get("jpeg_bytes")
    if raw is None:
        encoded = frame.get("image_base64")
        if not isinstance(encoded, str):
            raise RenderError("Нет JPEG evidence")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise RenderError("Неверный base64 evidence") from exc
    if not isinstance(raw, bytes):
        raise RenderError("JPEG evidence должен быть bytes")
    try:
        validate_jpeg(raw)
    except RenderError:
        if reencode is None:
            raise
        raw = reencode(raw)
        validate_jpeg(raw)
    return base64.b64encode(raw).decode("ascii")


def validate_jury(document: Mapping[str, Any], *, duration_sec: float | None = None) -> None:
    """Validate the frozen schema and additional documented renderer invariants."""
    duration_sec = _duration(duration_sec)
    error = next(_validator().iter_errors(document), None)
    if error is not None:
        # jsonschema's error text can contain evidence; never include its value.
        raise RenderError("Ответ не соответствует замороженной jury schema")
    try:
        json.dumps(document, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise RenderError("Ответ не является конечным JSON") from exc
    violations = document["violations"]
    if len(violations) > 5:
        raise RenderError("Не больше пяти нарушений")
    previous = -1.0
    for violation in violations:
        event = _number(violation["timecode_sec"])
        if event < previous or event < 0 or (duration_sec is not None and event > duration_sec):
            raise RenderError("Нарушения должны быть по времени в пределах видео")
        if event != round(event, 2):
            raise RenderError("Таймкод должен быть округлён до 0.01")
        previous = event
        if not violation["type"].strip() or not violation["description"].strip():
            raise RenderError("Тип и описание не могут быть пустыми")
        for index, frame in enumerate(violation["evidence_frames"]):
            stamp = _number(frame["timestamp_sec"])
            if (
                stamp < 0
                or stamp != round(stamp, 2)
                or (duration_sec is not None and stamp > duration_sec)
            ):
                raise RenderError("Таймкод evidence вне длительности видео")
            if index == 0 and stamp != event:
                raise RenderError("Первый evidence должен совпадать с событием")
            _jpeg(frame, None)


def render_jury(
    job_id: str,
    violations: Iterable[Mapping[str, Any]] = (),
    *,
    duration_sec: float | None = None,
    status: str = "done",
    safe_fallback: bool = True,
    reencode_jpeg: Callable[[bytes], bytes] | None = None,
) -> dict[str, Any]:
    """Render internal mappings into exactly the four frozen jury fields.

    A failed safe fallback returns ``failed`` with a null verdict; it never
    invents pixels, text or a no-violation verdict. Caller must persist this
    terminal status consistently. Optional reencoding belongs to the worker's
    evidence implementation, not the API process.
    """
    if not isinstance(job_id, str) or not job_id:
        raise RenderError("job_id обязателен")
    duration_sec = _duration(duration_sec)
    status = {"queued": "processing", "running": "processing"}.get(status, status)
    if status not in {"processing", "done", "failed"}:
        raise RenderError("Неизвестный статус")
    document: dict[str, Any] = {
        "job_id": job_id,
        "status": status,
        "verdict": None,
        "violations": [],
    }
    if status != "done":
        validate_jury(document)
        return document
    try:
        items = list(violations)
        if not safe_fallback and len(items) > 5:
            raise RenderError("Не больше пяти нарушений")
        normalized = []
        for item in items:
            event = _time(
                item.get("pts_event", item.get("timecode_sec")),
                duration_sec,
                safe_fallback,
            )
            title = item.get("type")
            description = item.get("description")
            if (
                not isinstance(title, str)
                or not title.strip()
                or not isinstance(description, str)
                or not description.strip()
            ):
                raise RenderError("Тип и описание обязательны")
            frames = list(item.get("evidence_frames", ()))
            if not frames or (not safe_fallback and len(frames) > 3):
                raise RenderError("Требуется 1–3 evidence кадра")
            evidence = []
            for index, frame in enumerate(frames[:3]):
                timestamp = _time(frame.get("timestamp_sec"), duration_sec, safe_fallback)
                if index == 0 and timestamp != event:
                    # Frame timestamp is the source PTS. Never relabel pixels
                    # with an invented timestamp; anchor the event to this PTS.
                    if not safe_fallback:
                        raise RenderError("Первый evidence должен совпадать с событием")
                    event = timestamp
                evidence.append(
                    {
                        "timestamp_sec": timestamp,
                        "image_base64": _jpeg(frame, reencode_jpeg if safe_fallback else None),
                    }
                )
            normalized.append(
                {
                    "type": title,
                    "timecode_sec": event,
                    "description": description,
                    "evidence_frames": evidence,
                }
            )
        normalized.sort(key=lambda item: item["timecode_sec"])
        document["violations"] = normalized[:5]
        document["verdict"] = "violation" if normalized else "no_violation"
        validate_jury(document, duration_sec=duration_sec)
    except (RenderError, TypeError, AttributeError, KeyError):
        if not safe_fallback:
            raise
        document.update(status="failed", verdict=None, violations=[])
        validate_jury(document)
    return document
