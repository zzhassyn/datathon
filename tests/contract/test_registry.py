"""Реестр правил: находит правило из нового пакета без центрального списка (P0-S0-04)."""

import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

from cashguard.contracts.catalog import SignalCode, ViolationCode
from cashguard.contracts.findings import RuleConfig, RuleContext
from cashguard.contracts.jobs import PerceptionLevel
from cashguard.contracts.perception import PerceptionPlan, PerceptionTrace
from cashguard.contracts.scene import SceneLayout, SceneModel
from cashguard.contracts.video import VideoMeta
from cashguard.pipeline.rules.base import BaseRule, register_rule
from cashguard.pipeline.rules.registry import discover, evaluate_all

RULE_MODULE = textwrap.dedent(
    """
    from cashguard.contracts.catalog import ViolationCode, SignalCode
    from cashguard.contracts.findings import Candidate, RuleContext, Signal
    from cashguard.pipeline.rules.base import BaseRule, BaseSignalRule, register_rule
    from cashguard.pipeline.rules.context import make_candidate, make_signal


    @register_rule
    class TempSafeOpen(BaseRule):
        code = ViolationCode.SAFE_OPEN
        version = "test"

        def evaluate(self, ctx: RuleContext) -> list[Candidate]:
            return [make_candidate(ctx, self.code, t_sec=99.0, start_sec=1.0, end_sec=120.0,
                                   confidence=0.9, rule_version=self.version)]


    @register_rule
    class TempCameraObstructed(BaseSignalRule):
        code = SignalCode.SIG_CAMERA_OBSTRUCTED

        def evaluate(self, ctx: RuleContext) -> list[Signal]:
            return [make_signal(self.code, t_sec=3.0, start_sec=2.0, end_sec=4.0, confidence=0.5)]
    """
)


def _context() -> RuleContext:
    video = VideoMeta(
        duration_sec=20.0,
        width=464,
        height=848,
        fps_avg=30.0,
        is_vfr=False,
        codec="h264",
        pix_fmt="yuv420p",
        start_pts_sec=0.0,
        time_base="1/600",
        sha256="0" * 64,
    )
    trace = PerceptionTrace(
        video=video,
        models={},
        plan=PerceptionPlan(
            level=PerceptionLevel.FULL,
            sample_fps=3,
            pose_fps=2,
            cls_fps=1.5,
            imgsz_variant="864x480",
        ),
        frames=[],
    )
    scene = SceneModel(
        layout=SceneLayout(zones=[], orientation="portrait", calibrated_from_sec=0, conf=1),
        roles=[],
        sessions=[],
        states=[],
        events=[],
    )
    return RuleContext(video=video, trace=trace, scene=scene, cfg=RuleConfig(code="base"))


@pytest.fixture
def temp_rules_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    pkg = tmp_path / "tmp_rules_pkg"
    (pkg / "group").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "group" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "group" / "safe_open.py").write_text(RULE_MODULE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield "tmp_rules_pkg"
    for name in [m for m in sys.modules if m.startswith("tmp_rules_pkg")]:
        del sys.modules[name]


def test_registry_discovers_rule_from_new_package(temp_rules_package: str) -> None:
    registry = discover([temp_rules_package])
    assert set(registry.rules) == {ViolationCode.SAFE_OPEN}
    assert set(registry.signals) == {SignalCode.SIG_CAMERA_OBSTRUCTED}


def test_registry_evaluate_all_clamps_and_sorts(temp_rules_package: str) -> None:
    registry = discover([temp_rules_package])
    cands, signals = evaluate_all(_context(), registry=registry)
    assert [c.code for c in cands] == [ViolationCode.SAFE_OPEN]
    assert cands[0].t_sec == 20.0, "таймкод зажат длительностью видео"
    assert signals[0].code == SignalCode.SIG_CAMERA_OBSTRUCTED


def test_registry_respects_disabled_config(temp_rules_package: str) -> None:
    registry = discover([temp_rules_package])
    configs = {"SAFE_OPEN": RuleConfig(code="SAFE_OPEN", enabled=False)}
    cands, _ = evaluate_all(_context(), configs, registry=registry)
    assert cands == []


def test_registry_default_package_imports_cleanly() -> None:
    registry = discover()
    assert registry.codes <= {str(c) for c in ViolationCode} | {str(c) for c in SignalCode}


def test_register_rule_rejects_unknown_code() -> None:
    with pytest.raises(TypeError):

        @register_rule
        class Bad(BaseRule):  # pyright: ignore[reportUnusedClass]
            code = "NOT_A_CODE"  # pyright: ignore[reportAssignmentType]

            def evaluate(self, ctx: RuleContext) -> list:  # pyright: ignore[reportMissingTypeArgument]
                return []
