"""Infrastructure adapter; the real stage runner is owned by S0/S3."""

import uuid

import av
import structlog

from cashguard.core.atomic import atomic_write_bytes, atomic_write_json
from cashguard.core.budget import Budget
from cashguard.jobs.repo import JobRecord
from cashguard.pipeline.render.ui import build_ui_result_fragment
from cashguard.pipeline.stub import StubDeadlineError, StubInputError, run_stub_analysis
from cashguard.workers.base import Worker


class PerceptionWorker(Worker):
    def process(self, job: JobRecord) -> None:
        try:
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
            directory = (
                self.settings.paths.job_dir(job.id) / f"attempt-{uuid.uuid4().hex}"
            )
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
        except (av.FFmpegError, OSError, ValueError, RuntimeError):
            # Public/API errors never include arbitrary codec text or local paths.
            self.repo.fail(
                job.id,
                "PIPELINE_CRASH",
                "Ошибка обработки синтетического сценария",
                lease_owner=self.name,
                lease_token=job.lease_token,
            )
