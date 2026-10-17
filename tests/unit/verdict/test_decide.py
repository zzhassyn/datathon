"""Вердикт по числовым кандидатам: никаких моделей и кадров."""

from typing import Literal

import pytest

from cashguard.contracts.catalog import ViolationCode
from cashguard.contracts.findings import Candidate, VlmAnswer, VlmAnswerItem, VlmReview
from cashguard.pipeline.verdict.decide import decide


def candidate(id="a", code="SAFE_OPEN", t=10.0, score=0.8, **updates):
    return Candidate.model_validate(
        dict(
            id=id,
            code=code,
            t_sec=t,
            start_sec=t,
            end_sec=t + 4,
            confidence=score,
            facts={},
            evidence=[],
            vlm_group="g",
            rule_version="1",
            verdict_impact=True,
        )
        | updates
    )


def config(**rules):
    configured = {
        code: {"enabled": True, "tau": 0.5, "ref_duration_sec": 1} | rules.get(code, {})
        for code in ViolationCode
    }
    return {"rules": configured, "vlm": {"scoring": {"no_conf_min": 0.8, "down_factor": 0.25}}}


def review(answer: Literal["yes", "no", "unclear"] = "no", confidence=0.9, qid="a", group="g"):
    return VlmReview(
        answers=[
            VlmAnswer(
                group_id=group,
                answers=[VlmAnswerItem(qid=qid, a=answer, c=confidence)],
                tokens_in=0,
                tokens_out=0,
                latency_ms=1,
                model_rev="synthetic",
            )
        ]
    )


def test_threshold_duration_and_non_verdict_candidates():
    cfg = config(SAFE_OPEN={"tau": 0.4, "ref_duration_sec": 8})
    result = decide(
        [
            candidate(),
            candidate(id="b", t=30, score=0.79),
            candidate(id="c", t=50, verdict_impact=False),
        ],
        None,
        cfg,
    )
    assert [(d.candidate.id, d.score, d.vlm_status) for d in result] == [("a", 0.4, "skipped")]


def test_event_without_duration_has_full_score():
    result = decide([candidate(end_sec=10)], None, config(SAFE_OPEN={"ref_duration_sec": 8}))
    assert result[0].score == 0.8


def test_disabled_and_untuned_rules_do_not_silently_use_default_threshold():
    assert decide([candidate()], None, config(SAFE_OPEN={"enabled": False, "tau": None})) == []
    with pytest.raises(ValueError, match="порог"):
        decide([candidate()], None, config(SAFE_OPEN={"tau": None}))


def test_merging_preserves_best_anchor_and_unions_interval_without_raising_score():
    result = decide([candidate(score=0.7), candidate(id="b", t=15, score=0.9)], None, config())
    assert len(result) == 1
    assert result[0].candidate.id == "b"
    assert result[0].candidate.t_sec == 15
    assert (result[0].candidate.start_sec, result[0].candidate.end_sec) == (10, 19)
    assert result[0].score == 0.9


def test_merging_does_not_span_distinct_tracks_or_sessions():
    candidates = [
        candidate(facts={"track_id": 1, "session_id": "one"}),
        candidate(id="b", t=11, facts={"track_id": 2, "session_id": "one"}),
        candidate(id="c", t=12, facts={"track_id": 1, "session_id": "two"}),
    ]
    assert len(decide(candidates, None, config())) == 3


def test_limit_selects_highest_scores_then_sorts_by_time():
    candidates = [candidate(id=str(i), t=i * 20, score=0.5 + i / 20) for i in range(7)]
    result = decide(candidates, None, config())
    assert [d.candidate.id for d in result] == ["2", "3", "4", "5", "6"]


@pytest.mark.parametrize("answer,status", [("yes", "confirmed"), ("unclear", "unverified")])
def test_vlm_never_increases_score(answer, status):
    result = decide([candidate()], review(answer), config())
    assert result[0].score == 0.8 and result[0].vlm_status == status


def test_confident_no_only_downweights_matching_candidate_and_group():
    cfg = config(SAFE_OPEN={"tau": 0.1})
    assert decide([candidate()], review(), cfg)[0].score == 0.2
    for mismatched in [review(qid="another"), review(group="another"), review(confidence=0.7)]:
        result = decide([candidate()], mismatched, cfg)
        assert result[0].score == 0.8 and result[0].vlm_status == "unverified"


def test_missing_group_cannot_apply_an_unrelated_vlm_answer():
    result = decide([candidate(vlm_group=None)], review(), config())
    assert result[0].score == 0.8 and result[0].vlm_status == "unverified"


def test_conflicting_or_repeated_vlm_answers_are_unverified():
    first = review()
    both = first.model_copy(update={"answers": first.answers + review("yes").answers})
    result = decide([candidate()], both, config())
    assert result[0].score == 0.8 and result[0].vlm_status == "unverified"


def test_final_threshold_cannot_promote_a_previously_rejected_candidate():
    cfg = config(SAFE_OPEN={"tau": 0.85, "tau_final": 0.1})
    assert decide([candidate()], None, cfg) == []
    assert decide([candidate()], review("yes"), cfg) == []


def test_downweighting_retains_same_deduplication_anchor():
    cfg = config(SAFE_OPEN={"tau": 0.3})
    items = [candidate(score=0.9), candidate(id="b", t=11, score=0.8)]
    assert [d.candidate.id for d in decide(items, None, cfg)] == ["a"]
    assert decide(items, review(), cfg) == []


def test_suppression_requires_explicit_same_track_and_time_window():
    cfg = config(
        SAFE_OPEN={"suppresses": [{"code": "SAFE_KEY_LEFT", "same_track": True, "window_sec": 2}]}
    )
    safe = candidate(facts={"track_id": 1})
    key = candidate(id="b", code="SAFE_KEY_LEFT", t=11, facts={"track_id": 1})
    assert [d.candidate.code for d in decide([safe, key], None, cfg)] == [ViolationCode.SAFE_OPEN]
    for other in [
        key.model_copy(update={"facts": {"track_id": 2}}),
        key.model_copy(update={"facts": {}}),
        key.model_copy(update={"t_sec": 20.0, "end_sec": 24.0}),
    ]:
        assert len(decide([safe, other], None, cfg)) == 2


@pytest.mark.parametrize(
    "cfg",
    [
        {"merge_gap_sec": -1},
        {"max_violations": 0},
        {"max_violations": 6},
        {"rules": {"SAFE_OPEN": {"ref_duration_sec": 0}}},
        {"vlm": {"scoring": {"down_factor": 1.1}}},
        {"vlm": {"scoring": {"no_conf_min": float("nan")}}},
    ],
)
def test_invalid_config_fails_closed(cfg):
    with pytest.raises(ValueError):
        decide([candidate()], None, cfg)


def test_invalid_intervals_and_duplicate_ids_fail_closed():
    with pytest.raises(ValueError):
        decide([candidate(start_sec=15)], None, config())
    with pytest.raises(ValueError):
        decide([candidate(), candidate(t=30)], None, config())


def test_vlm_does_not_promote_sixth_candidate_after_top_five_changes():
    items = [candidate(id=str(i), t=i * 20, score=0.6 + i / 20) for i in range(6)]
    before = {d.candidate.id for d in decide(items, None, config())}
    after = {d.candidate.id for d in decide(items, review(qid="5"), config())}
    assert after <= before


def test_suppression_cycles_fail_closed():
    cfg = config(
        SAFE_OPEN={"suppresses": [{"code": "SAFE_KEY_LEFT", "window_sec": 2}]},
        SAFE_KEY_LEFT={"suppresses": [{"code": "SAFE_OPEN", "window_sec": 2}]},
    )
    with pytest.raises(ValueError, match="подавлен"):
        decide([candidate()], None, cfg)


@pytest.mark.parametrize("cfg", [{}, {"rules": {"SAFE_OPEN": {"enabled": True}}}])
def test_unconfigured_verdict_code_cannot_use_guessed_threshold(cfg):
    with pytest.raises(ValueError, match="порог"):
        decide([candidate()], None, cfg)


@pytest.mark.parametrize("unchecked", [VlmReview(skipped_reason="vlm_deadline"), VlmReview()])
def test_skipped_or_unverified_vlm_keeps_rules_threshold(unchecked):
    cfg = config(SAFE_OPEN={"tau": 0.5, "tau_final": 0.9})
    before = decide([candidate()], None, cfg)
    after = decide([candidate()], unchecked, cfg)
    assert [(d.candidate.id, d.score) for d in before] == [(d.candidate.id, d.score) for d in after]


def test_empty_candidates_require_at_least_one_enabled_tuned_rule():
    for cfg in [
        {},
        {"rules": {"SAFE_OPEN": {"enabled": False}}},
        {"rules": {"SAFE_OPEN": {"enabled": True}}},
    ]:
        with pytest.raises(ValueError):
            decide([], None, cfg)
