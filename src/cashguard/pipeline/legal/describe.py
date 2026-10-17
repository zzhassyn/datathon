"""Русские описания из фактов правила и уже проверенного каталога норм."""

import logging
import math
from collections.abc import Mapping
from functools import cache

from cashguard.contracts.catalog import (
    ActCode,
    CatalogEntry,
    LegalRef,
    LegalStrength,
    ViolationCode,
)
from cashguard.contracts.findings import Decision, Fact
from cashguard.contracts.scene import SceneModel
from cashguard.pipeline.legal.catalog import load_catalog

log = logging.getLogger(__name__)


@cache
def _citations() -> dict[ActCode, str]:
    return {act.act_code: act.cite_ru for act in load_catalog().acts.acts}


def _format(value: Fact) -> str:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Числовой факт должен быть конечным")
        return f"{value:.1f}".replace(".", ",")
    return str(value)


def _applies(ref: LegalRef, facts: dict[str, Fact]) -> bool:
    if ref.applies_if is None:
        return True
    value = facts.get(ref.applies_if)
    # Strings such as "false"/"true" are not proof of a rule's condition.
    return isinstance(value, int | float) and value == 1


def describe(
    decision: Decision, scene: SceneModel, catalog: Mapping[ViolationCode, CatalogEntry]
) -> tuple[str, list[LegalRef]]:
    """Замороженный порт legal.describe; VLM-текст не используется.

    SceneModel передаётся по общему контракту; описание опирается только на
    подтверждённые факты кандидата, не выводит дополнительные факты из сцены.
    """
    candidate = decision.candidate
    entry = catalog[candidate.code]
    facts = candidate.facts
    if set(entry.required_facts) <= facts.keys():
        observation = entry.template_ru.format_map(
            {key: _format(value) for key, value in facts.items()}
        )
    else:
        log.warning("Недостаточно фактов для шаблона %s", candidate.code)
        time_ru = _format(float(candidate.t_sec))
        observation = f"В {time_ru} с наблюдается: {entry.title_ru}."
    legal = [ref for ref in entry.legal if _applies(ref, facts)]
    if not legal:
        return f"{observation} Нормативное основание для этого события не установлено.", []
    primary = legal[0]
    act_cite = _citations()[primary.act_code]
    if primary.clause is None:
        basis = (
            f"Прямой нормы нет; пункт {act_cite} не определён (FAQ 31); "
            "требуется проверка человеком."
        )
    elif primary.strength == LegalStrength.DIRECT:
        basis = f"Основание: {primary.clause} {act_cite}."
    elif primary.strength == LegalStrength.INDIRECT:
        basis = f"Может свидетельствовать о несоблюдении {primary.clause} {act_cite}."
    else:
        basis = f"Прямой нормы нет; ближайшая — {primary.clause} {act_cite}."
    return f"{observation} {basis}", legal
