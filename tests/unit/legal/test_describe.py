"""Описание только из утверждённых шаблонов и применимых норм."""

import pytest

from cashguard.contracts.catalog import ViolationCode
from cashguard.contracts.findings import Candidate, Decision, VlmStatus
from cashguard.contracts.scene import SceneLayout, SceneModel
from cashguard.pipeline.legal.catalog import load_catalog
from cashguard.pipeline.legal.describe import describe


@pytest.fixture
def scene():
    return SceneModel(
        layout=SceneLayout(zones=[], orientation="unknown", calibrated_from_sec=0, conf=0),
        roles=[],
        sessions=[],
        states=[],
        events=[],
    )


def decision(code="SAFE_OPEN", facts=None):
    return Decision(
        candidate=Candidate(
            id="a",
            code=ViolationCode(code),
            t_sec=10,
            start_sec=10,
            end_sec=14,
            confidence=0.8,
            facts=facts or {},
            evidence=[],
            vlm_group=None,
            rule_version="1",
            verdict_impact=True,
        ),
        score=0.8,
        vlm_status=VlmStatus.SKIPPED,
    )


def test_safe_open_description_snapshot(scene, snapshot):
    text, legal = describe(
        decision(facts={"t_open": 7.0, "t_start": 10.0, "open_unattended_sec": 4.0}),
        scene,
        load_catalog().entries,
    )
    assert text == snapshot
    assert len(legal) == 1 and legal[0].strength == "indirect"
    assert "Может свидетельствовать о несоблюдении п. 20 ч. 2" in text


def test_missing_required_fact_uses_observation_fallback(scene):
    text, _ = describe(decision(), scene, load_catalog().entries)
    assert text.startswith("В 10,0 с наблюдается: Сейф оставлен открытым.")
    assert "{" not in text


@pytest.mark.parametrize(
    "fact,expected",
    [(1, True), (True, True), (0, False), (False, False), ("false", False), ("true", False)],
)
def test_conditional_norm_requires_explicit_true_fact(scene, fact, expected):
    catalog = load_catalog().entries
    original = catalog[ViolationCode.SAFE_OPEN]
    ref = original.legal[0].model_copy(update={"applies_if": "during_service"})
    catalog[original.code] = original.model_copy(update={"legal": [ref]})
    text, legal = describe(decision(facts={"during_service": fact}), scene, catalog)
    assert bool(legal) is expected
    if not expected:
        assert "Нормативное основание для этого события не установлено." in text


def test_none_and_faq31_use_approved_wording(scene):
    catalog = load_catalog().entries
    entry = catalog[ViolationCode.SAFE_OPEN]
    ref = entry.legal[0].model_copy(
        update={"strength": "none", "clause": None, "quote_ru": None, "note_ru": "FAQ 31"}
    )
    catalog[entry.code] = entry.model_copy(update={"legal": [ref]})
    text, _ = describe(decision(), scene, catalog)
    assert "Прямой нормы нет; пункт " in text
    assert "не определён (FAQ 31); требуется проверка человеком." in text


@pytest.mark.parametrize("code", list(ViolationCode))
def test_all_existing_templates_can_render_without_invented_facts(scene, code):
    catalog = load_catalog().entries
    facts = dict.fromkeys(catalog[code].required_facts, 2.0)
    text, legal = describe(decision(code, facts), scene, catalog)
    assert "{" not in text and "}" not in text
    assert text.startswith(catalog[code].template_ru.format_map(dict.fromkeys(facts, "2,0")))
    assert all(ref in catalog[code].legal for ref in legal)
