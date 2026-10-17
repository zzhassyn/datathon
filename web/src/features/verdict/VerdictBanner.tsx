import { AlertTriangle, CheckCircle2, Clock3 } from "lucide-react";
import type { JobView } from "../../shared/api/decode";
import { ru } from "../../shared/i18n/ru";
export function VerdictBanner({ job }: { job: JobView }) {
  const final = job.status === "done";
  const provisional = !final && job.provisional;
  const title = provisional
    ? ru.provisional
    : final
      ? job.verdict === "violation"
        ? ru.violation
        : ru.clean
      : job.status === "queued"
        ? ru.queued
        : ru.running;
  const degraded =
    final &&
    ((job.result_level !== null && job.result_level !== "full") || job.degradations.length > 0);
  return (
    <section
      className={`verdict ${final ? (job.verdict === "violation" ? "warning" : "clean") : "pending"}`}
      role="status"
      aria-live="polite"
    >
      <div className="verdict-main">
        {final ? (
          job.verdict === "violation" ? (
            <AlertTriangle size={21} />
          ) : (
            <CheckCircle2 size={21} />
          )
        ) : (
          <Clock3 size={21} />
        )}
        <div>
          <h2>{title}</h2>
          {provisional ? (
            <p>{ru.provisionalHint}</p>
          ) : final && job.verdict === "no_violation" ? (
            <p>{ru.cleanHint}</p>
          ) : null}
        </div>
        {final && job.verdict === "violation" && (
          <span className="event-count">{job.violations.length}</span>
        )}
      </div>
      {degraded && (
        <details className="degradation">
          <summary>{ru.degraded}</summary>
          {job.result_level === "rules" && <p>{ru.rulesOnly}</p>}
          {job.result_level === "partial" && <p>{ru.partial}</p>}
          {job.degradations.map((d) => (
            <p key={`${d.stage}-${d.reason_ru}`}>{d.reason_ru}</p>
          ))}
        </details>
      )}
    </section>
  );
}
