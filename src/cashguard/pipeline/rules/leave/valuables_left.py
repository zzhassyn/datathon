"""Ценности после подтверждённого выхода; пропажа трека не считается выходом."""

import math

from cashguard.contracts.catalog import ViolationCode
from cashguard.contracts.findings import Candidate, EvidenceKind, EvidenceRequest, RuleContext
from cashguard.contracts.scene import Role, SceneEventKind, StateSubject, ZoneKind
from cashguard.pipeline.rules.base import BaseRule, register_rule


def evaluate_scene(scene, *, min_duration_sec=2.0):
    if not math.isfinite(min_duration_sec) or min_duration_sec <= 0:
        raise ValueError("Длительность должна быть положительной")
    staff = {r.track_id for r in scene.roles if r.role == Role.CASHIER}
    found = []
    for event in scene.events:
        if event.kind != SceneEventKind.EXIT or not staff.intersection(event.track_ids):
            continue
        confirmed = float(event.data.get("confirmed_at", float("inf")))
        end = max(event.t + min_duration_sec, confirmed)
        # Tolerance of one sampling step: a clip may end just before ``end``.
        if float(event.data.get("room_empty_until", event.t)) + 0.6 < end:
            continue
        end = min(end, float(event.data.get("room_empty_until", end)) + 0.6)
        items = [
            s
            for s in scene.states
            if s.zone == ZoneKind.DESK
            and s.subject in (StateSubject.CASH_ON_DESK, StateSubject.BAG_ON_DESK)
            and s.track_id is not None
            and s.conf >= 0.5
            and s.start_t <= event.t
            and s.end_t + 0.6 >= end
        ]
        # The catalogue's direct clause covers cash. A bag alone needs the bank's policy.
        if not any(s.subject == StateSubject.CASH_ON_DESK for s in items):
            continue
        names = []
        if any(s.subject == StateSubject.CASH_ON_DESK for s in items):
            names.append("наличные")
        if any(s.subject == StateSubject.BAG_ON_DESK for s in items):
            names.append("инкассаторская сумка")
        found.append(
            Candidate(
                id=f"valuables-left-{len(found) + 1}",
                code=ViolationCode.VALUABLES_LEFT,
                t_sec=event.t,
                start_sec=event.t,
                end_sec=end,
                confidence=min(event.conf, *(s.conf for s in items)),
                facts={"t_exit": event.t, "items_ru": " и ".join(names)},
                evidence=[
                    EvidenceRequest(kind=EvidenceKind.EVENT, t_sec=event.t, label="После выхода"),
                    EvidenceRequest(
                        kind=EvidenceKind.CONTEXT, t_sec=end, label="Ценности остаются"
                    ),
                ],
                rule_version="calibrated-temporal-v1",
                verdict_impact=True,
            )
        )
    return found


@register_rule
class ValuablesLeftRule(BaseRule):
    code = ViolationCode.VALUABLES_LEFT
    version = "calibrated-temporal-v1"

    def evaluate(self, ctx: RuleContext) -> list[Candidate]:
        return evaluate_scene(
            ctx.scene, min_duration_sec=float(ctx.cfg.params.get("min_duration_sec", 2))
        )
