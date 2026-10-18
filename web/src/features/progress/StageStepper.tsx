import type { JobView } from "../../shared/api/decode";
import { ru } from "../../shared/i18n/ru";
import { timecode } from "../../shared/lib/format";
export function StageStepper({ job }: { job: JobView }) {
  return (
    <section className="progress-panel panel">
      <div className="section-heading">
        <h2>
          {job.status === "done" || job.status === "failed"
            ? ru.stagesHeading
            : job.status === "queued"
              ? ru.queued
              : ru.running}
        </h2>
        <span className="mono muted">
          {ru.elapsed} {timecode(job.elapsed_sec)}
        </span>
      </div>
      {job.status === "queued" && job.queue_position !== null && (
        <p className="muted">
          {ru.queue}: {job.queue_position}
        </p>
      )}
      {job.eta_sec !== null && (
        <p className="muted">
          {ru.remaining} {timecode(job.eta_sec)}
        </p>
      )}
      <ol className="stages">
        {job.stages.map((s) => (
          <li key={s.code}>
            <span className={`stage-dot ${s.status}`} aria-hidden="true" />
            <div>
              <span>{ru.stages[s.code] || s.code}</span>
              <span className="muted">{ru.stageStatus[s.status] || s.status}</span>
              {s.note_ru && <p>{s.note_ru}</p>}
              {s.status === "running" && s.progress !== null && (
                <progress
                  max={1}
                  value={Math.max(0, Math.min(1, s.progress))}
                  aria-label={ru.stages[s.code] || s.code}
                />
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
