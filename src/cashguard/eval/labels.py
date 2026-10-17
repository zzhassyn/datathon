"""Numerical label projection, filename normalization and canonical duplicates."""

from __future__ import annotations

import csv
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

ALLOWED_COLUMNS = (
    "file",
    "label",
    "timecode_sec",
    "type_code",
    "type_ru",
    "clean_pair",
    "confidence",
    "scene",
)
DUPLICATES = {"X-47": "X-4", "X-48": "X-5"}


@dataclass(frozen=True)
class LabelEvent:
    code: str
    timecode_sec: float
    confidence: str = ""


@dataclass(frozen=True)
class LabelVideo:
    video_id: str
    scene: str
    events: tuple[LabelEvent, ...]
    source_ids: tuple[str, ...] = ()


def normalize_video_id(value: str) -> str:
    """Normalize documented X/Х filenames, refusing paths and arbitrary IDs."""
    value = re.sub(r"\s+", "", value).replace("Х", "X").replace("х", "x").upper()
    value = re.sub(r"\.(MOV|MP4)$", "", value)
    if not re.fullmatch(r"X-\d+", value):
        raise ValueError("Неверный id размеченного видео")
    return value


def canonical_video_id(value: str) -> str:
    normalized = normalize_video_id(value)
    return DUPLICATES.get(normalized, normalized)


def labels_from_rows(rows: Iterable[Mapping[str, str]]) -> dict[str, LabelVideo]:
    """Read only allowlisted keys, never free-text frame descriptions."""
    grouped: dict[str, dict] = {}
    seen_sources = set()
    for source in rows:
        row = {column: source.get(column, "") for column in ALLOWED_COLUMNS}
        source_id = normalize_video_id(row["file"])
        if source_id in seen_sources:
            raise ValueError("Повторная строка одного исходного видео")
        seen_sources.add(source_id)
        video_id = DUPLICATES.get(source_id, source_id)
        if row["label"] not in {"violation", "no_violation"}:
            raise ValueError("Неизвестная метка вердикта")
        if row["scene"] not in {"L1", "L2", "L3", "L4", "L5"}:
            raise ValueError("Неизвестная сцена")
        state = grouped.setdefault(
            video_id,
            {"scene": row["scene"], "label": row["label"], "events": [], "sources": []},
        )
        if state["scene"] != row["scene"] or state["label"] != row["label"]:
            raise ValueError("Противоречивые метки дубликатов")
        state["sources"].append(source_id)
        if row["label"] == "violation":
            timecode = float(row["timecode_sec"])
            if (
                not math.isfinite(timecode)
                or timecode < 0
                or not row["type_code"]
                or row["type_code"].startswith("SIG_")
            ):
                raise ValueError("Некорректное эталонное событие")
            state["events"].append(
                LabelEvent(row["type_code"], timecode, row["confidence"])
            )
        elif row["type_code"] or row["timecode_sec"]:
            raise ValueError("Чистая метка не может содержать событие")
    return {
        video_id: LabelVideo(
            video_id,
            state["scene"],
            tuple(sorted(state["events"], key=lambda event: event.timecode_sec)),
            tuple(sorted(state["sources"])),
        )
        for video_id, state in grouped.items()
    }


def load_labels(path: str | Path) -> dict[str, LabelVideo]:
    """Project CSV immediately onto the allowlist; discard other columns.

    CSV quoting still has to be parsed to locate fields, but non-allowlisted
    cells are never interpreted, retained in results or included in errors.
    """
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader, [])
        if any(column not in header for column in ALLOWED_COLUMNS):
            raise ValueError("В разметке отсутствуют обязательные столбцы")
        if len(set(header)) != len(header):
            raise ValueError("Повторные имена столбцов разметки")
        indices = {column: header.index(column) for column in ALLOWED_COLUMNS}

        def projected():
            for row in reader:
                if len(row) != len(header):
                    raise ValueError("Неверное число столбцов разметки")
                yield {column: row[index].strip() for column, index in indices.items()}

        return labels_from_rows(projected())
