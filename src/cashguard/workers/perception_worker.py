"""Infrastructure adapter; the real stage runner is owned by S0/S3."""

import time
import uuid
from pathlib import Path

import structlog
from av.error import FFmpegError

from cashguard.contracts.findings import AnalysisResult
from cashguard.core.atomic import atomic_write_bytes, atomic_write_json
from cashguard.core.budget import Budget
from cashguard.jobs.repo import JobRecord
from cashguard.pipeline import runner
from cashguard.pipeline.ingest.preview import make_preview
from cashguard.pipeline.ingest.probe import probe
from cashguard.pipeline.render.analysis import render_analysis
from cashguard.pipeline.render.ui import build_ui_result_fragment
from cashguard.pipeline.stub import StubDeadlineError, StubInputError, run_stub_analysis
from cashguard.workers.base import Worker


class PerceptionWorker(Worker):
    def process(self, job: JobRecord) -> None:
        try:
            if job.source == "ui":
                meta = probe(job.input_path, deep=True)
                preview = self.settings.paths.job_dir(job.id) / "preview.mp4"
                if meta.needs_transcode:
                    make_preview(
                        Path(job.input_path),
                        meta,
                        preview,
                        transcode=True,
                        deadline=min(time.time() + 30, job.tiers.finalize_at - 5),
                    )
            if self.settings.pipeline_mode != "stub":
                self.process_pipeline(job)
                return
            result = run_stub_analysis(
                job.input_path,
                job.id,
                budget=Budget(job.tiers),
                duration_sec=job.duration_sec,
                delay_sec=self.settings.stub_delay_sec,
                progress=lambda fraction: self.repo.report_progress(
                    job.id,
                    self.name,
                    "perception",
                    fraction,
                    "Синтетический stub-сценарий",
                    lease_token=job.lease_token,
                ),
            )
            if result.jury["status"] != "done":
                self.repo.fail(
                    job.id,
                    "INTERNAL",
                    "Синтетический результат не прошёл проверку",
                    lease_owner=self.name,
                    lease_token=job.lease_token,
                )
                return
            # Each lease publishes immutable files. A late worker cannot replace
            # the winning CAS result after recovery or deadline finalization.
            directory = self.settings.paths.job_dir(job.id) / f"attempt-{uuid.uuid4().hex}"
            for name, jpeg in result.evidence.items():
                atomic_write_bytes(directory / "evidence" / name, jpeg)
            jury_path = atomic_write_json(directory / "jury.json", result.jury)
            names = [[name] for name in result.evidence]
            ui = build_ui_result_fragment(job.id, result.jury, evidence_names=names)
            for index, violation in enumerate(ui["violations"], 1):
                violation.update(
                    {
                        "id": f"stub-event-{index}",
                        "code": "STUB_EVENT",
                        "start_sec": violation["timecode_sec"],
                        "end_sec": violation["timecode_sec"],
                        "confidence": 1.0,
                        "vlm_status": "skipped",
                        "provisional": False,
                        "legal": [],
                    }
                )
                for frame in violation["evidence"]:
                    frame.update(
                        {
                            "kind": "event",
                            "width": result.width,
                            "height": result.height,
                        }
                    )
            ui_path = atomic_write_json(directory / "ui_result.json", ui)
            atomic_write_json(
                directory / "analysis.json",
                {
                    "mode": "stub",
                    "synthetic": True,
                    "scenario": result.scenario,
                    "notice_ru": "Это проверка инфраструктуры; модели AI не подключены.",
                },
            )
            self.repo.set_provisional(
                job.id,
                lease_owner=self.name,
                lease_token=job.lease_token,
                jury_path=jury_path,
                ui_result_path=ui_path,
                verdict=result.jury["verdict"],
                n_violations=len(result.jury["violations"]),
                degradations=["vlm_disabled"],
            )
            published = self.repo.finalize(
                job.id,
                lease_owner=self.name,
                lease_token=job.lease_token,
                jury_path=jury_path,
                ui_result_path=ui_path,
                verdict=result.jury["verdict"],
                n_violations=len(result.jury["violations"]),
                result_level="rules",
            )
            structlog.get_logger().info(
                "stub_result_saved",
                job_id=job.id,
                published=published,
                verdict=result.jury["verdict"],
                violations=len(result.jury["violations"]),
            )
        except StubInputError as error:
            self.repo.fail(
                job.id,
                "UNSUPPORTED_VIDEO",
                str(error),
                lease_owner=self.name,
                lease_token=job.lease_token,
            )
        except StubDeadlineError as error:
            self.repo.fail(
                job.id,
                "PIPELINE_CRASH",
                str(error),
                lease_owner=self.name,
                lease_token=job.lease_token,
            )
        except (FFmpegError, OSError, ValueError, RuntimeError):
            # Public/API errors never include arbitrary codec text or local paths.
            self.repo.fail(
                job.id,
                "PIPELINE_CRASH",
                "Ошибка обработки видео",
                lease_owner=self.name,
                lease_token=job.lease_token,
            )

    def process_pipeline(self, job: JobRecord) -> None:
        """Frozen runner boundary. Startup remains disabled until model stages are delivered."""
        mode = runner.PipelineMode(self.settings.pipeline_mode)
        stages = runner.load_stages(mode)
        directory = self.settings.paths.job_dir(job.id) / f"attempt-{uuid.uuid4().hex}"
        directory.mkdir(parents=True, exist_ok=False)
        result = runner.run_perception_stage(
            runner.StageJob(job.id, directory, Path(job.input_path), job.source, mode),
            Budget(job.tiers),
            stages,
        )
        self.publish_analysis(job, result, directory / "evidence")

    def publish_analysis(self, job: JobRecord, result: AnalysisResult, evidence_dir: Path) -> bool:
        """Durably publish validated model output, fenced by the current worker lease."""
        if result.job_id != job.id or result.video.sha256 != job.sha256:
            raise ValueError("analysis identity does not match the claimed input")
        root = self.settings.paths.job_dir(job.id).resolve()
        if not evidence_dir.resolve().is_relative_to(root):
            raise ValueError("evidence directory is outside the claimed job")
        rendered = render_analysis(result, evidence_dir)
        directory = self.settings.paths.job_dir(job.id) / f"attempt-{uuid.uuid4().hex}"
        for name, jpeg in rendered.evidence.items():
            atomic_write_bytes(directory / "evidence" / name, jpeg)
        jury_path = atomic_write_json(
            directory / "jury.json", rendered.jury.model_dump(mode="json")
        )
        ui_path = atomic_write_json(directory / "ui_result.json", rendered.ui)
        atomic_write_json(directory / "analysis.json", result.model_dump(mode="json"))
        published = self.repo.finalize(
            job.id,
            lease_owner=self.name,
            lease_token=job.lease_token,
            jury_path=jury_path,
            ui_result_path=ui_path,
            verdict=result.verdict,
            n_violations=len(rendered.jury.violations),
            result_level=result.result_level,
            degradations=[str(item.reason) for item in result.degradations],
        )
        structlog.get_logger().info("analysis_published", job_id=job.id, published=published)
        return published
