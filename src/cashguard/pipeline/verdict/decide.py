"""Детерминированный вердикт; VLM может только убрать кандидата из результата."""

import math
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cashguard.contracts.catalog import ViolationCode
from cashguard.contracts.findings import Candidate, Decision, VlmReview, VlmStatus


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class _Suppression(_Config):
    code: ViolationCode
    same_track: bool = True
    window_sec: float = Field(ge=0)


class _Rule(_Config):
    enabled: bool = False
    tau: float | None = Field(default=None, ge=0, le=1)
    tau_final: float | None = Field(default=None, ge=0, le=1)
    ref_duration_sec: float | None = Field(default=None, gt=0)
    suppresses: list[_Suppression] = Field(default_factory=list)


class _Scoring(_Config):
    no_conf_min: float = Field(ge=0, le=1)
    down_factor: float = Field(ge=0, le=1)


class _Vlm(_Config):
    scoring: _Scoring | None = None


class _Policy(_Config):
    rules: dict[ViolationCode, _Rule] = Field(default_factory=dict)
    vlm: _Vlm = Field(default_factory=_Vlm)
    merge_gap_sec: float = Field(default=10, ge=0)
    max_violations: int = Field(default=5, ge=1, le=5, strict=True)


def _check_suppression(policy: _Policy) -> None:
    visited: set[ViolationCode] = set()
    pending: set[ViolationCode] = set()

    def visit(code: ViolationCode) -> None:
        if code in pending:
            raise ValueError("Цикл подавления кодов")
        if code in visited:
            return
        pending.add(code)
        for suppression in policy.rules.get(code, _Rule()).suppresses:
            visit(suppression.code)
        pending.remove(code)
        visited.add(code)

    for code in policy.rules:
        visit(code)


def _identity(candidate: Candidate) -> tuple[Any, ...]:
    return (
        candidate.code,
        candidate.facts.get("track_id"),
        candidate.facts.get("session_id"),
        candidate.vlm_group,
    )


def _merge(items: list[Decision], gap: float) -> list[Decision]:
    groups: dict[tuple[Any, ...], list[list[Decision]]] = {}
    for item in sorted(items, key=lambda d: (d.candidate.t_sec, d.candidate.id)):
        clusters = groups.setdefault(_identity(item.candidate), [])
        if not clusters or item.candidate.t_sec - clusters[-1][-1].candidate.t_sec > gap:
            clusters.append([])
        clusters[-1].append(item)
    merged = []
    for clusters in groups.values():
        for cluster in clusters:
            winner = min(cluster, key=lambda d: (-d.score, d.candidate.t_sec, d.candidate.id))
            anchor = winner.candidate.model_copy(
                update={
                    "start_sec": min(d.candidate.start_sec for d in cluster),
                    "end_sec": max(d.candidate.end_sec for d in cluster),
                }
            )
            merged.append(winner.model_copy(update={"candidate": anchor}))
    return merged


def _review(item: Decision, review: VlmReview | None, cfg: _Scoring | None) -> Decision:
    if review is None or review.skipped_reason is not None:
        return item
    if cfg is None:
        raise ValueError("Не настроены параметры проверки VLM")
    candidate = item.candidate
    answers = [
        answer
        for group in review.answers
        if candidate.vlm_group is not None and group.group_id == candidate.vlm_group
        for answer in group.answers
        if answer.qid == candidate.id
    ]
    score, status = item.score, VlmStatus.UNVERIFIED
    # Duplicate/conflicting answers are invalid evidence, not a reason to discard an event.
    if len(answers) == 1:
        answer = answers[0]
        if answer.a == "yes":
            status = VlmStatus.CONFIRMED
        elif answer.a == "no" and answer.c >= cfg.no_conf_min:
            score, status = score * cfg.down_factor, VlmStatus.DOWNWEIGHTED
    return item.model_copy(update={"score": score, "vlm_status": status})


def _suppressed(item: Decision, winners: list[Decision], policy: _Policy) -> bool:
    candidate = item.candidate
    for other in winners:
        if other.candidate.id == candidate.id:
            continue
        rule = policy.rules.get(other.candidate.code, _Rule())
        for suppression in rule.suppresses:
            same_track = candidate.facts.get("track_id") is not None and candidate.facts.get(
                "track_id"
            ) == other.candidate.facts.get("track_id")
            if (
                suppression.code == candidate.code
                and abs(candidate.t_sec - other.candidate.t_sec) <= suppression.window_sec
                and (not suppression.same_track or same_track)
            ):
                return True
    return False


def decide(
    cands: list[Candidate], review: VlmReview | None, cfg: Mapping[str, Any]
) -> list[Decision]:
    """Слияние → VLM → пороги → подавление → лучшие пять, затем порядок по времени.

    Конфиг: ``rules[CODE] = {enabled, tau, tau_final, ref_duration_sec, suppresses}``,
    ``vlm.scoring = {no_conf_min, down_factor}``. Значения подбирают LOSO/eval-full.
    ``qid`` VLM равен ID кандидата; дополнительно проверяется ``vlm_group``.
    Отклонённые кандидаты сохраняет runner в candidates.json, не в ответе жюри.
    """
    policy = _Policy.model_validate(dict(cfg))
    _check_suppression(policy)
    enabled = [rule for rule in policy.rules.values() if rule.enabled]
    if not enabled:
        raise ValueError("Нет включённых правил с настроенным порогом")
    if any(rule.tau is None for rule in enabled):
        raise ValueError("Не настроен порог включённого правила")
    if len({c.id for c in cands}) != len(cands):
        raise ValueError("Повторяющийся ID кандидата")
    scored = []
    for candidate in cands:
        if (
            not all(
                math.isfinite(value)
                for value in (
                    candidate.t_sec,
                    candidate.start_sec,
                    candidate.end_sec,
                    candidate.confidence,
                )
            )
            or not candidate.start_sec <= candidate.t_sec <= candidate.end_sec
        ):
            raise ValueError("Некорректный интервал кандидата")
        if not candidate.verdict_impact:
            continue
        rule = policy.rules.get(candidate.code)
        if rule is None:
            raise ValueError(f"Не настроен порог {candidate.code}")
        if not rule.enabled:
            continue
        if rule.tau is None:
            raise ValueError(f"Не настроен порог {candidate.code}")
        duration = candidate.end_sec - candidate.start_sec
        if duration and rule.ref_duration_sec is None:
            raise ValueError(f"Не настроена длительность {candidate.code}")
        factor = (
            min(duration / rule.ref_duration_sec, 1) if duration and rule.ref_duration_sec else 1
        )
        scored.append(
            Decision(
                candidate=candidate,
                score=candidate.confidence * factor,
                vlm_status=VlmStatus.SKIPPED,
            )
        )
    merged = _merge(scored, policy.merge_gap_sec)
    base_eligible = [
        d for d in merged if d.score >= (policy.rules.get(d.candidate.code, _Rule()).tau or 0)
    ]
    selected = [d for d in base_eligible if not _suppressed(d, base_eligible, policy)]
    selected.sort(
        key=lambda d: (-d.score, d.candidate.t_sec, str(d.candidate.code), d.candidate.id)
    )
    eligible = []
    # Freeze the provisional shortlist before review, including its anchor and suppression.
    # Otherwise downweighting one event could promote a sixth event into the final result.
    for original in selected[: policy.max_violations]:
        rule = policy.rules.get(original.candidate.code, _Rule())
        if rule.tau is None:
            raise ValueError(f"Не настроен порог {original.candidate.code}")
        reviewed = _review(original, review, policy.vlm.scoring)
        checked = reviewed.vlm_status in {VlmStatus.CONFIRMED, VlmStatus.DOWNWEIGHTED}
        threshold = max(rule.tau, rule.tau_final or 0) if checked else rule.tau
        if reviewed.score >= threshold:
            eligible.append(reviewed)
    return sorted(
        eligible, key=lambda d: (d.candidate.t_sec, str(d.candidate.code), d.candidate.id)
    )
