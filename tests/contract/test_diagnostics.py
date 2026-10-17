"""Diagnostics must preserve uncertainty and stay compatible with old results."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cashguard.contracts.diagnostics import (
    RuleCondition,
    RuleExplanation,
    VisibilityAssessment,
    VisibilityCheck,
)
from cashguard.contracts.ui import UiJobView


def condition(**changes):
    return {
        "id": "desk",
        "text_ru": "Тестовое условие",
        "status": "unknown",
        "reason_ru": "Синтетическая причина",
        "evidence_indices": [],
        **changes,
    }


@pytest.mark.parametrize("status", ["met", "not_met"])
def test_known_condition_requires_actual_evidence(status):
    with pytest.raises(ValidationError):
        RuleCondition.model_validate(condition(status=status))
    assert (
        RuleCondition.model_validate(condition(status=status, evidence_indices=[0])).status
        == status
    )


@pytest.mark.parametrize("indices", [[-1], [True], [0.5], [0, 0]])
def test_evidence_indices_are_unique_nonnegative_integers(indices):
    with pytest.raises(ValidationError):
        RuleCondition.model_validate(condition(evidence_indices=indices))


def test_unknown_remains_unknown_and_reference_bounds_are_checked():
    explanation = RuleExplanation(
        rule_version="test-v1", conditions=[RuleCondition.model_validate(condition())]
    )
    assert explanation.conditions[0].status == "unknown"
    explanation.validate_evidence_count(0)
    anchored = RuleExplanation(
        rule_version="test-v1",
        conditions=[RuleCondition.model_validate(condition(status="met", evidence_indices=[1]))],
    )
    with pytest.raises(ValueError):
        anchored.validate_evidence_count(1)


@pytest.mark.parametrize(
    "start,end", [(2, 1), (-1, 2), (0, float("inf")), (float("nan"), 1), (1, None), (True, 2)]
)
def test_visibility_rejects_invalid_intervals(start, end):
    with pytest.raises(ValidationError):
        VisibilityCheck(
            id="test",
            target_ru="Тест",
            status="limited",
            reason_ru="Синтетика",
            start_sec=start,
            end_sec=end,
        )


def test_visibility_known_needs_window_and_duration_is_bounded():
    with pytest.raises(ValidationError):
        VisibilityCheck(id="test", target_ru="Тест", status="visible", reason_ru="Синтетика")
    check = VisibilityCheck(
        id="test", target_ru="Тест", status="limited", reason_ru="Синтетика", start_sec=0, end_sec=2
    )
    report = VisibilityAssessment(method_version="test-v1", checks=[check])
    report.validate_duration(2)
    with pytest.raises(ValueError):
        report.validate_duration(1.9)
    with pytest.raises(ValidationError):
        VisibilityAssessment(method_version="test-v1", checks=[check, check])


def test_old_ui_result_defaults_to_no_assessment():
    path = Path(__file__).parents[1] / "fixtures/ui/04_done_violations.json"
    raw = json.loads(path.read_text())
    raw.pop("visibility", None)
    for event in raw["violations"]:
        event.pop("explanation", None)
    view = UiJobView.model_validate(raw)
    assert view.visibility is None
    assert all(event.explanation is None for event in view.violations)
    raw["violations"][0]["explanation"] = {
        "rule_version": "test-v1",
        "conditions": [condition(status="met", evidence_indices=[99])],
    }
    with pytest.raises(ValidationError):
        UiJobView.model_validate(raw)


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), -1])
def test_unknown_visibility_still_requires_valid_source_duration(duration):
    report = VisibilityAssessment(
        method_version="test-v1",
        checks=[
            VisibilityCheck(
                id="unknown", target_ru="Тест", status="unknown", reason_ru="Нет оценки"
            )
        ],
    )
    with pytest.raises(ValueError):
        report.validate_duration(duration)
