"""Общие помощники для правил: выборки из сцены и сборка кандидатов. Чистый Python."""

import hashlib
import itertools
from collections.abc import Iterator

from cashguard.contracts.catalog import SignalCode, ViolationCode
from cashguard.contracts.findings import Candidate, EvidenceRequest, Fact, RuleContext, Signal
from cashguard.contracts.scene import (
    Role,
    SceneEvent,
    SceneEventKind,
    StateInterval,
    StateSubject,
    TrackRole,
)


def states(
    ctx: RuleContext, subject: StateSubject, value: str | None = None
) -> list[StateInterval]:
    """Интервалы состояния субъекта (по времени), опционально с фильтром по значению."""
    found = [s for s in ctx.scene.states if s.subject == subject]
    if value is not None:
        found = [s for s in found if s.value == value]
    return sorted(found, key=lambda s: s.start_t)


def state_at(ctx: RuleContext, subject: StateSubject, t: float) -> StateInterval | None:
    """Интервал состояния, который покрывает момент ``t``."""
    for s in states(ctx, subject):
        if s.start_t <= t <= s.end_t:
            return s
    return None


def events(ctx: RuleContext, *kinds: SceneEventKind) -> list[SceneEvent]:
    found = [e for e in ctx.scene.events if not kinds or e.kind in kinds]
    return sorted(found, key=lambda e: e.t)


def tracks(ctx: RuleContext, role: Role) -> list[TrackRole]:
    return sorted((r for r in ctx.scene.roles if r.role == role), key=lambda r: r.first_t)


def param(ctx: RuleContext, name: str, default: float) -> float:
    """Числовой параметр из ``configs/rules/<CODE>.yaml`` с значением по умолчанию."""
    value = ctx.cfg.params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"{ctx.cfg.code}.{name}: ожидается число"
        raise TypeError(msg)
    return float(value)


def candidate_id(code: ViolationCode | SignalCode, t_sec: float) -> str:
    """Стабильный id: одинаковый для одного кода и момента при повторных прогонах."""
    raw = f"{code}@{t_sec:.2f}".encode()
    return hashlib.sha1(raw, usedforsecurity=False).hexdigest()[:12]


def make_candidate(
    ctx: RuleContext,
    code: ViolationCode,
    *,
    t_sec: float,
    start_sec: float,
    end_sec: float,
    confidence: float,
    rule_version: str,
    facts: dict[str, Fact] | None = None,
    evidence: list[EvidenceRequest] | None = None,
    vlm_group: str | None = None,
    verdict_impact: bool = True,
) -> Candidate:
    """Кандидат с таймкодом внутри длительности видео и уверенностью 0..1."""
    duration = ctx.video.duration_sec
    t = min(max(t_sec, 0.0), duration)
    return Candidate(
        id=candidate_id(code, t),
        code=code,
        t_sec=round(t, 2),
        start_sec=round(min(max(start_sec, 0.0), t), 2),
        end_sec=round(min(max(end_sec, t), duration), 2),
        confidence=min(max(confidence, 0.0), 1.0),
        facts=facts or {},
        evidence=evidence or [],
        vlm_group=vlm_group,
        rule_version=rule_version,
        verdict_impact=verdict_impact,
    )


def make_signal(
    code: SignalCode,
    *,
    t_sec: float,
    start_sec: float,
    end_sec: float,
    confidence: float,
    facts: dict[str, Fact] | None = None,
) -> Signal:
    return Signal(
        id=candidate_id(code, t_sec),
        code=code,
        t_sec=round(max(t_sec, 0.0), 2),
        start_sec=round(max(start_sec, 0.0), 2),
        end_sec=round(max(end_sec, t_sec), 2),
        confidence=min(max(confidence, 0.0), 1.0),
        facts=facts or {},
    )


def pairwise(items: list[StateInterval]) -> Iterator[tuple[StateInterval, StateInterval]]:
    """Соседние интервалы: удобно искать смену состояния."""
    return itertools.pairwise(items)
