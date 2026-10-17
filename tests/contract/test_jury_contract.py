"""Контракт жюри (ТЗ §10): схема и Pydantic-модели принимают и отвергают одно и то же."""

import base64
import copy
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from cashguard.contracts.jury import MAX_VIOLATIONS, JuryJobResponse

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "src/cashguard/contracts/schemas/jury_job.schema.json"
EXAMPLE_PATH = REPO_ROOT / "tests/fixtures/jury_example.json"

FROZEN_SHA256 = "908120fee6d4d4654a69f1109d052dc94ee7b3d0c6f1ac5797f4d8f86a1ff79f"
"""sha256 канонического JSON схемы (docs/api.md, раздел 10.1). Не зависит от форматирования.
Меняется только вместе с одобренной человеком правкой схемы (ADR-0006)."""

Doc = dict[str, Any]


def _schema() -> Doc:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _example() -> Doc:
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


def _schema_ok(doc: Doc) -> bool:
    return not list(Draft202012Validator(_schema()).iter_errors(doc))


def _model_ok(doc: Doc) -> bool:
    try:
        JuryJobResponse.model_validate(doc)
    except ValidationError:
        return False
    return True


def test_frozen_schema_hash_unchanged() -> None:
    canon = json.dumps(_schema(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert hashlib.sha256(canon.encode("utf-8")).hexdigest() == FROZEN_SHA256, (
        "замороженная схема жюри изменилась: нужна правка с одобрения человека и новый хеш"
    )


def test_tz_example_frame_is_real_jpeg() -> None:
    frame = _example()["violations"][0]["evidence_frames"][0]["image_base64"]
    raw = base64.b64decode(frame, validate=True)
    assert raw[:3] == b"\xff\xd8\xff"
    assert raw[-2:] == b"\xff\xd9"


def test_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(_schema())


def test_tz_example_passes_schema_and_model() -> None:
    example = _example()
    assert _schema_ok(example)
    assert _model_ok(example)


def test_model_round_trip_keeps_exact_shape() -> None:
    example = _example()
    dumped = JuryJobResponse.model_validate(example).model_dump(mode="json")
    assert dumped == example
    assert list(dumped) == ["job_id", "status", "verdict", "violations"]


VALID_STATES: dict[str, Doc] = {
    "processing": {"job_id": "a" * 32, "status": "processing", "verdict": None, "violations": []},
    "failed": {"job_id": "a" * 32, "status": "failed", "verdict": None, "violations": []},
    "done_clean": {
        "job_id": "a" * 32,
        "status": "done",
        "verdict": "no_violation",
        "violations": [],
    },
}


@pytest.mark.parametrize("name", list(VALID_STATES))
def test_valid_states_accepted(name: str) -> None:
    doc = VALID_STATES[name]
    assert _schema_ok(doc)
    assert _model_ok(doc)


def _first_violation(doc: Doc) -> Doc:
    return doc["violations"][0]


MUTATIONS: dict[str, Callable[[Doc], None]] = {
    "extra_top_key": lambda d: d.update(extra=1),
    "extra_violation_key": lambda d: _first_violation(d).update(signals=[]),
    "extra_frame_key": lambda d: _first_violation(d)["evidence_frames"][0].update(url="x"),
    "done_with_null_verdict": lambda d: d.update(verdict=None),
    "violation_without_items": lambda d: d.update(violations=[]),
    "no_violation_with_items": lambda d: d.update(verdict="no_violation"),
    "processing_with_verdict": lambda d: d.update(status="processing"),
    "unknown_status": lambda d: d.update(status="queued"),
    "four_frames": lambda d: _first_violation(d).update(
        evidence_frames=_first_violation(d)["evidence_frames"] * 4
    ),
    "zero_frames": lambda d: _first_violation(d).update(evidence_frames=[]),
    "empty_description": lambda d: _first_violation(d).update(description=""),
    "empty_type": lambda d: _first_violation(d).update(type=""),
    "negative_timecode": lambda d: _first_violation(d).update(timecode_sec=-1.0),
    "data_uri_prefix": lambda d: _first_violation(d)["evidence_frames"][0].update(
        image_base64="data:image/jpeg;base64,/9j/4AAQ"
    ),
    "missing_verdict_key": lambda d: d.pop("verdict"),
}


@pytest.mark.parametrize("name", list(MUTATIONS))
def test_mutations_rejected_by_schema_and_model(name: str) -> None:
    doc = copy.deepcopy(_example())
    MUTATIONS[name](doc)
    assert not _schema_ok(doc), f"схема пропустила мутацию {name}"
    assert not _model_ok(doc), f"модель пропустила мутацию {name}"


def test_model_only_invariants() -> None:
    """Инварианты, которые JSON Schema не выражает, держит модель."""
    base = _example()
    violation = _first_violation(base)

    shifted = copy.deepcopy(base)
    _first_violation(shifted)["evidence_frames"][0]["timestamp_sec"] = violation["timecode_sec"] + 1
    assert _schema_ok(shifted)
    assert not _model_ok(shifted), "первый кадр должен совпадать с таймкодом"

    unsorted = copy.deepcopy(base)
    later = copy.deepcopy(violation)
    later["timecode_sec"] = violation["timecode_sec"] + 5
    later["evidence_frames"][0]["timestamp_sec"] = later["timecode_sec"]
    unsorted["violations"] = [later, violation]
    assert not _model_ok(unsorted), "нарушения должны идти по времени"

    too_many = copy.deepcopy(base)
    too_many["violations"] = [copy.deepcopy(violation) for _ in range(MAX_VIOLATIONS + 1)]
    assert not _model_ok(too_many)


def test_model_schema_has_same_keys_as_frozen_schema() -> None:
    """Pydantic-модель и рукописная схема описывают одни и те же поля на всех уровнях."""
    frozen = _schema()
    generated = JuryJobResponse.model_json_schema(mode="serialization")
    assert set(generated["properties"]) == set(frozen["properties"])
    assert set(generated["required"]) == set(frozen["required"])
    gen_defs = generated["$defs"]
    assert set(gen_defs["JuryViolation"]["properties"]) == set(
        frozen["$defs"]["violation"]["properties"]
    )
    assert set(gen_defs["JuryEvidenceFrame"]["properties"]) == set(
        frozen["$defs"]["evidence_frame"]["properties"]
    )
