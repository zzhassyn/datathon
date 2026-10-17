"""Сборка K4 на синтетических портах, без запуска или оценки моделей."""

import gzip
import json
from dataclasses import replace

import av
import numpy as np
import pytest

from cashguard.contracts.catalog import ViolationCode
from cashguard.contracts.findings import (
    AnalysisVersions,
    Candidate,
    EvidenceImage,
    EvidenceKind,
    RuleConfig,
)
from cashguard.contracts.jobs import JobStatus, PerceptionLevel
from cashguard.contracts.perception import FrameObservation, PerceptionPlan, PerceptionTrace
from cashguard.contracts.scene import SceneLayout, SceneModel
from cashguard.contracts.ui import UiJobView
from cashguard.contracts.video import FrameIndex, FrameIndexEntry, FrameRef, VideoMeta
from cashguard.pipeline import runner
from cashguard.pipeline.legal.catalog import load_catalog
from cashguard.pipeline.legal.describe import describe
from cashguard.pipeline.render.analysis import render_analysis
from cashguard.pipeline.runtime import StageResources, bind_stages, stage_factory, with_progress
from cashguard.pipeline.verdict.decide import decide


class Budget:
    remaining_sec = 100.0

    def now(self):
        return 0.0

    def remaining(self, tier):
        return self.remaining_sec

    def allows(self, est_sec, tier="vlm_start_cutoff", margin_sec=15):
        return self.remaining_sec > est_sec + margin_sec


class Store:
    def __init__(self, times):
        self.times = times

    def index(self):
        return FrameIndex(
            frames=[
                FrameIndexEntry(idx=i, t_sec=t, pts=round(t * 1000), path=f"synthetic-{i}")
                for i, t in enumerate(self.times)
            ]
        )

    def nearest(self, t_sec):
        t = min(self.times, key=lambda at: (abs(at - t_sec), at))
        return FrameRef(t_sec=t, path="synthetic")

    def get(self, t_sec):
        return np.zeros((64, 64, 3), dtype=np.uint8)

    def put(self, frame):
        raise AssertionError("Synthetic store is prepopulated")


class Backend:
    name = "stub"

    def warmup(self):
        pass

    def info(self):
        return {"synthetic": "true"}

    def run(self, samples, plan, progress):
        raise AssertionError("Perception stage owns backend invocation")


@pytest.fixture
def setup(tmp_path):
    video = VideoMeta(
        duration_sec=10,
        width=64,
        height=64,
        fps_avg=25,
        is_vfr=True,
        codec="h264",
        pix_fmt="yuv420p",
        start_pts_sec=0,
        time_base="1/1000",
        sha256="0" * 64,
    )
    times = [0, 0.6, 1.3, 2.1, 3.0, 4.0, 5.2, 6.1, 7.0, 8.0, 9.1, 9.6]
    plan = PerceptionPlan(
        level=PerceptionLevel.FAST, sample_fps=2, pose_fps=0, cls_fps=0, imgsz_variant="480x864"
    )
    trace = PerceptionTrace(
        video=video,
        models={},
        plan=plan,
        frames=[FrameObservation(idx=i, t_sec=t, detections=[]) for i, t in enumerate(times)],
    )
    scene = SceneModel(
        layout=SceneLayout(zones=[], orientation="synthetic", calibrated_from_sec=0, conf=1),
        roles=[],
        sessions=[],
        states=[],
        events=[],
    )
    catalog = load_catalog()
    resources = StageResources(
        plan=plan,
        backend=Backend(),
        store=Store(times),
        catalog=catalog,
        versions=AnalysisVersions(
            git_sha="synthetic", models_hash="synthetic", config_hash=catalog.config_hash
        ),
        expected_models={},
        rule_configs={"SAFE_OPEN": RuleConfig(code="SAFE_OPEN", tau=0.5)},
        scene_config={},
        verdict_config={
            "rules": {"SAFE_OPEN": {"enabled": True, "tau": 0.5, "ref_duration_sec": 1}}
        },
        progress=lambda *args: None,
    )
    events = []
    candidates = [
        Candidate(
            id="safe",
            code=ViolationCode.SAFE_OPEN,
            t_sec=4.2,
            start_sec=4.1,
            end_sec=8,
            confidence=0.9,
            rule_version="synthetic",
            verdict_impact=True,
            facts={"t_open": 1.3, "t_start": 4.2, "open_unattended_sec": 3.8},
        )
    ]

    def perception(*args):
        events.append("perception")
        args[-1]("perception", 0.5, "Синтетический прогресс")
        return trace

    def evidence(decisions, store, out):
        events.append("evidence")
        out.mkdir(parents=True, exist_ok=True)
        result = {}
        for i, decision in enumerate(decisions):
            encoder = av.CodecContext.create("mjpeg", "w")
            encoder.width, encoder.height, encoder.pix_fmt = 64, 64, "yuvj420p"
            frame = av.VideoFrame.from_ndarray(store.get(0), format="rgb24")
            jpeg = b"".join(bytes(packet) for packet in encoder.encode(frame))
            name = f"{i:02}_event.jpg"
            (out / name).write_bytes(jpeg)
            result[decision.candidate.id] = [
                EvidenceImage(
                    kind=EvidenceKind.EVENT,
                    file=name,
                    timestamp_sec=store.nearest(decision.candidate.t_sec).t_sec,
                    width=64,
                    height=64,
                )
            ]
        return result

    job = runner.StageJob(
        "a" * 32, tmp_path / "attempt", tmp_path / "input", "ui", runner.PipelineMode.STUB
    )

    def render_ui(result):
        fragment = render_analysis(result, job.job_dir / "evidence").ui
        return UiJobView(
            job_id=result.job_id,
            status=JobStatus.DONE,
            pipeline_mode="stub",
            created_at="2026-10-18T00:00:00Z",
            elapsed_sec=0,
            stages=[],
            **fragment,
        )

    stages = runner.StageSet(
        probe=lambda path: video,
        make_preview=lambda *args: events.append("preview"),
        perception_run=perception,
        scene_build=lambda *args: scene,
        rules_evaluate=lambda *args: (list(candidates), []),
        verdict_decide=decide,
        evidence_render=evidence,
        legal_describe=describe,
        vlm_plan=lambda *args: [],
        render_jury=lambda result, out: render_analysis(result, out).jury,
        render_ui=render_ui,
    )
    return (
        job,
        Budget(),
        bind_stages(stages, lambda *args: resources),
        resources,
        trace,
        events,
        candidates,
    )


def test_runs_real_orchestration_and_publishes_consistent_artifacts(setup):
    job, budget, stages, _, _, events, _ = setup
    result = runner.run_perception_stage(job, budget, stages)
    assert result.verdict == "violation" and result.result_level == "rules"
    assert result.violations[0].timecode_sec == 4.0
    assert result.violations[0].start_sec == 4.0
    assert result.violations[0].evidence[0].timestamp_sec == 4.0
    assert events == ["preview", "perception", "evidence"]
    assert result.degradations[0].reason == "vlm_disabled"
    assert set(result.timings) >= {"probe", "perception", "rules", "evidence"}
    assert json.loads((job.job_dir / "analysis.json").read_text())["job_id"] == job.job_id
    assert json.loads(gzip.decompress((job.job_dir / "trace.json.gz").read_bytes()))["frames"]
    jury = json.loads((job.job_dir / "jury.json").read_text())
    assert set(jury) == {"job_id", "status", "verdict", "violations"}
    assert jury["violations"][0]["timecode_sec"] == 4.0
    assert json.loads((job.job_dir / "ui_result.json").read_text())["violations"][0]["legal"]


def test_clean_result_requires_successful_stages_and_nonempty_trace(setup):
    job, budget, stages, _, _, _, candidates = setup
    candidates.clear()
    result = runner.run_perception_stage(job, budget, stages)
    assert result.verdict == "no_violation" and not result.violations


@pytest.mark.parametrize("fault", ["empty", "sha", "plan", "time", "order", "schema", "models"])
def test_invalid_trace_never_becomes_clean(setup, fault):
    job, budget, stages, _, trace, _, _ = setup
    data = trace.model_dump(mode="json")
    if fault == "empty":
        data["frames"] = []
    elif fault == "sha":
        data["video"]["sha256"] = "1" * 64
    elif fault == "plan":
        data["plan"]["sample_fps"] = 1
    elif fault == "time":
        data["frames"][-1]["t_sec"] = 12
    elif fault == "order":
        data["frames"][2]["t_sec"] = 0.5
    elif fault == "schema":
        data["schema_version"] = 2
    else:
        data["models"] = {"detector": {"version": "other", "sha256": "1" * 64}}
    bad_trace = PerceptionTrace.model_validate(data)
    stages = replace(stages, perception_run=lambda *args: bad_trace)
    with pytest.raises(ValueError):
        runner.run_perception_stage(job, budget, stages)
    assert not (job.job_dir / "analysis.json").exists()


def test_expired_budget_stops_before_probe(setup):
    job, budget, stages, _, _, _, _ = setup
    budget.remaining_sec = 0
    stages = replace(stages, probe=lambda path: pytest.fail("probe must not run"))
    with pytest.raises(TimeoutError):
        runner.run_perception_stage(job, budget, stages)


def test_perception_progress_checks_deadline_and_does_not_publish_partial_clean(setup):
    job, budget, stages, _, trace, _, _ = setup

    def expired(*args):
        budget.remaining_sec = 0
        args[-1]("perception", 0.5, "Синтетический прогресс")
        return trace

    with pytest.raises(TimeoutError):
        runner.run_perception_stage(job, budget, replace(stages, perception_run=expired))
    assert not (job.job_dir / "analysis.json").exists()


@pytest.mark.parametrize(
    "fault", ["missing", "non_jpeg", "wrong_timestamp", "wrong_kind", "escaped"]
)
def test_bad_evidence_never_publishes_a_result(setup, fault):
    job, budget, stages, _, _, _, _ = setup
    original = stages.evidence_render

    def bad_evidence(*args):
        images = original(*args)
        frame = images["safe"][0]
        if fault == "missing":
            return {}
        if fault == "non_jpeg":
            (args[-1] / frame.file).write_bytes(b"invalid")
        elif fault == "wrong_timestamp":
            images["safe"] = [frame.model_copy(update={"timestamp_sec": 4.2})]
        elif fault == "wrong_kind":
            images["safe"] = [frame.model_copy(update={"kind": "context"})]
        elif fault == "escaped":
            images["safe"] = [frame.model_copy(update={"file": "../escape.jpg"})]
        return images

    with pytest.raises((ValueError, OSError)):
        runner.run_perception_stage(job, budget, replace(stages, evidence_render=bad_evidence))
    assert not (job.job_dir / "analysis.json").exists()


def test_jury_source_skips_preview(setup):
    job, budget, stages, _, _, events, _ = setup
    runner.run_perception_stage(replace(job, source="jury"), budget, stages)
    assert "preview" not in events


def test_factory_registration_is_explicit_scoped_and_mode_specific(setup):
    _, _, stages, _, _, _, _ = setup
    with pytest.raises(RuntimeError, match="не подключен"):
        runner.load_stages(runner.PipelineMode.REPLAY)
    with stage_factory(runner.PipelineMode.STUB, lambda: stages):
        assert runner.load_stages(runner.PipelineMode.STUB) is stages
        with pytest.raises(RuntimeError):
            runner.load_stages(runner.PipelineMode.REPLAY)
    with pytest.raises(RuntimeError):
        runner.load_stages(runner.PipelineMode.STUB)


def test_missing_resource_binding_fails_explicitly(setup):
    job, budget, stages, _, _, _, _ = setup
    plain = runner.StageSet(
        **{key: getattr(stages, key) for key in runner.StageSet.__dataclass_fields__}
    )
    with pytest.raises(RuntimeError, match="ресурс"):
        runner.run_perception_stage(job, budget, plain)


def test_progress_binding_reports_stages_and_restores_factory_context(setup):
    job, budget, stages, _, _, _, _ = setup
    reports = []
    bound = with_progress(stages, lambda *args: reports.append(args))
    result = runner.run_perception_stage(job, budget, bound)
    assert result.verdict == "violation"
    assert {report[0] for report in reports} >= {"probe", "perception", "rules", "evidence"}
    assert ("perception", 0.5, "Синтетический прогресс") in reports
    with stage_factory(runner.PipelineMode.STUB, lambda: stages):
        with stage_factory(runner.PipelineMode.STUB, lambda: bound):
            assert runner.load_stages(runner.PipelineMode.STUB) is bound
        assert runner.load_stages(runner.PipelineMode.STUB) is stages


@pytest.mark.parametrize("fault", ["invented", "score", "duplicates", "interval"])
def test_invalid_verdict_stage_cannot_publish_an_event(setup, fault):
    job, budget, stages, _, _, _, _ = setup
    original = stages.verdict_decide

    def invalid(*args):
        items = original(*args)
        if fault == "invented":
            return [
                items[0].model_copy(
                    update={"candidate": items[0].candidate.model_copy(update={"id": "invented"})}
                )
            ]
        if fault == "score":
            return [items[0].model_copy(update={"score": 1.0})]
        if fault == "interval":
            return [
                items[0].model_copy(
                    update={"candidate": items[0].candidate.model_copy(update={"end_sec": 20.0})}
                )
            ]
        return items * 2

    with pytest.raises(ValueError):
        runner.run_perception_stage(job, budget, replace(stages, verdict_decide=invalid))
    assert not (job.job_dir / "analysis.json").exists()


@pytest.mark.parametrize("fault", ["pts", "early_stop", "late_start", "missing_middle"])
def test_incomplete_trace_or_non_pts_store_fails_closed(setup, fault):
    job, budget, stages, resources, trace, _, _ = setup
    if fault == "pts":
        original = resources.store.index

        def wrong_pts():
            index = original()
            frames = list(index.frames)
            frames[3] = frames[3].model_copy(update={"pts": 5000})
            return index.model_copy(update={"frames": frames})

        resources.store.index = wrong_pts
    else:
        frames = (
            trace.frames[:-4]
            if fault == "early_stop"
            else [trace.frames[0], trace.frames[-1]]
            if fault == "missing_middle"
            else trace.frames[3:]
        )
        trace = trace.model_copy(update={"frames": frames})
        stages = replace(stages, perception_run=lambda *args: trace)
    with pytest.raises(ValueError):
        runner.run_perception_stage(job, budget, stages)
    assert not (job.job_dir / "analysis.json").exists()


def test_exception_in_scene_never_becomes_clean(setup):
    job, budget, stages, _, _, _, candidates = setup
    candidates.clear()

    def broken_scene(*args):
        raise RuntimeError("Синтетический сбой стадии")

    with pytest.raises(RuntimeError):
        runner.run_perception_stage(job, budget, replace(stages, scene_build=broken_scene))
    assert not (job.job_dir / "analysis.json").exists()


def test_worker_binds_pipeline_progress_to_current_lease(setup, tmp_path, monkeypatch):
    from cashguard.core.settings import Settings
    from cashguard.workers.perception_worker import PerceptionWorker

    _, _, stages, _, trace, _, _ = setup
    settings = Settings(data_dir=tmp_path / "runtime")
    settings.paths.ensure()
    worker = PerceptionWorker(settings, name="synthetic-worker")
    worker.repo.create_job(
        input_path="synthetic", sha256=trace.video.sha256, duration_sec=10, source="ui"
    )
    claimed = worker.repo.claim_next(worker.name)
    assert claimed is not None
    worker.repo.db.connect().execute("UPDATE jobs SET progress_at=0 WHERE id=?", (claimed.id,))

    def check_binding(job, budget, bound):
        bound.resource_factory(job, trace.video, budget).progress("rules", 0.5, "Проверка правил")
        raise RuntimeError("Остановка синтетического прогона")

    monkeypatch.setattr(runner, "run_perception_stage", check_binding)
    with (
        stage_factory(runner.PipelineMode.STUB, lambda: stages),
        pytest.raises(RuntimeError, match="Остановка"),
    ):
        worker.process_pipeline(claimed)
    actual = worker.repo.get_job(claimed.id)
    assert actual.stage_code == "rules" and actual.progress == 0.5
    assert actual.lease_token == claimed.lease_token
    worker.repo.db.close()


def test_runner_cannot_confirm_clean_without_any_enabled_rules(setup):
    job, budget, stages, resources, _, _, candidates = setup
    candidates.clear()
    resources = replace(resources, rule_configs={})
    stages = replace(stages, resource_factory=lambda *args: resources)
    with pytest.raises(ValueError, match="правил"):
        runner.run_perception_stage(job, budget, stages)
