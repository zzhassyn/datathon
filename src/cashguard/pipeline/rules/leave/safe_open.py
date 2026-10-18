"""Открытый сейф вне работы у него; неподтверждённое отсутствие не используется."""

import math

from cashguard.contracts.catalog import ViolationCode
from cashguard.contracts.findings import Candidate, EvidenceKind, EvidenceRequest, RuleContext
from cashguard.contracts.scene import StateSubject, ZoneKind
from cashguard.pipeline.rules.base import BaseRule, register_rule


def evaluate_scene(scene, *, min_duration_sec=2.0):
    if not math.isfinite(min_duration_sec) or min_duration_sec <= 0:
        raise ValueError("Длительность должна быть положительной")
    safes = [
        s
        for s in scene.states
        if s.subject == StateSubject.SAFE and s.value == "open" and s.conf >= 0.5
    ]
    away = [
        s
        for s in scene.states
        if s.subject == StateSubject.CASHIER_PRESENT
        and s.value == "away_from_safe"
        and s.zone == ZoneKind.SAFE
        and s.conf >= 0.5
    ]
    found = []
    for safe in safes:
        for state in away:
            start, end = max(safe.start_t, state.start_t), min(safe.end_t, state.end_t)
            if end - start < min_duration_sec:
                continue
            found.append(
                Candidate(
                    id=f"safe-open-{len(found) + 1}",
                    code=ViolationCode.SAFE_OPEN,
                    t_sec=start,
                    start_sec=start,
                    end_sec=end,
                    confidence=min(safe.conf, state.conf),
                    facts={
                        "t_open": safe.start_t,
                        "t_start": start,
                        "open_unattended_sec": end - start,
                    },
                    evidence=[
                        EvidenceRequest(kind=EvidenceKind.EVENT, t_sec=start, label="Сейф открыт"),
                        EvidenceRequest(
                            kind=EvidenceKind.CONTEXT, t_sec=end, label="Кассир не у сейфа"
                        ),
                    ],
                    rule_version="calibrated-temporal-v1",
                    verdict_impact=True,
                )
            )
    return found


@register_rule
class SafeOpenRule(BaseRule):
    code = ViolationCode.SAFE_OPEN
    version = "calibrated-temporal-v1"

    def evaluate(self, ctx: RuleContext) -> list[Candidate]:
        return evaluate_scene(
            ctx.scene, min_duration_sec=float(ctx.cfg.params.get("min_duration_sec", 2))
        )
