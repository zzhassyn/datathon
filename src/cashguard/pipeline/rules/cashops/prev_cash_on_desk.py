"""Смена клиента при тех же деньгах; принадлежность требует отдельного события передачи."""

from cashguard.contracts.catalog import ViolationCode
from cashguard.contracts.findings import Candidate, EvidenceKind, EvidenceRequest, RuleContext
from cashguard.contracts.scene import Role, SceneEventKind, StateSubject, ZoneKind
from cashguard.pipeline.rules.base import BaseRule, register_rule


def evaluate_scene(scene):
    clients = {r.track_id for r in scene.roles if r.role == Role.CLIENT}
    exits = [
        e
        for e in scene.events
        if e.kind == SceneEventKind.EXIT and clients.intersection(e.track_ids)
    ]
    entries = [
        e
        for e in scene.events
        if e.kind == SceneEventKind.ENTER and clients.intersection(e.track_ids)
    ]
    found = []
    for entry in entries:
        prior = [
            e
            for e in exits
            if float(e.data.get("confirmed_at", float("inf"))) <= entry.t
            and not set(e.track_ids).intersection(entry.track_ids)
        ]
        if not prior:
            continue
        exit_event = max(prior, key=lambda e: e.t)
        for cash in scene.states:
            if (
                cash.subject != StateSubject.CASH_ON_DESK
                or cash.zone != ZoneKind.DESK
                or cash.track_id is None
                or cash.conf < 0.5
                or not cash.start_t < exit_event.t < entry.t <= cash.end_t
            ):
                continue
            handoff = next(
                (
                    e
                    for e in scene.events
                    if e.kind == SceneEventKind.HANDOFF_CASH
                    and cash.start_t <= e.t <= exit_event.t
                    and cash.track_id in e.track_ids
                    and clients.intersection(exit_event.track_ids).intersection(e.track_ids)
                    and e.data.get("operation_confirmed") is True
                    and e.data.get("direction") == "to_cashier"
                    and e.conf >= 0.5
                ),
                None,
            )
            if handoff is None:
                continue
            found.append(
                Candidate(
                    id=f"previous-cash-{len(found) + 1}",
                    code=ViolationCode.PREV_CASH_ON_DESK,
                    t_sec=entry.t,
                    start_sec=exit_event.t,
                    end_sec=entry.t,
                    confidence=min(entry.conf, exit_event.conf, cash.conf, handoff.conf),
                    facts={"t_prev_exit": exit_event.t, "t_enter": entry.t},
                    evidence=[
                        EvidenceRequest(
                            kind=EvidenceKind.CONTEXT,
                            t_sec=exit_event.t,
                            label="Предыдущий клиент ушёл",
                        ),
                        EvidenceRequest(
                            kind=EvidenceKind.EVENT, t_sec=entry.t, label="Следующий клиент вошёл"
                        ),
                    ],
                    rule_version="calibrated-temporal-v1",
                    verdict_impact=True,
                )
            )
            break
    return found


@register_rule
class PreviousCashRule(BaseRule):
    code = ViolationCode.PREV_CASH_ON_DESK
    version = "calibrated-temporal-v1"

    def evaluate(self, ctx: RuleContext) -> list[Candidate]:
        return evaluate_scene(ctx.scene)
