import { StageStepper } from "../features/progress/StageStepper";
import { HttpError } from "../shared/api/errors";
import { useJob } from "../shared/api/queries";
import { ru } from "../shared/i18n/ru";
import { validJobId } from "../shared/lib/urlState";
import { KeyGate } from "../shared/ui/KeyGate";
import { Link } from "../shared/ui/Link";
export function JobPage({ id }: { id: string }) {
  const q = useJob(id);
  return (
    <section className="page">
      <div className="section-heading">
        <h1 tabIndex={-1}>{ru.jobTitle}</h1>
        <Link className="text-link" href="/">
          {ru.newJob}
        </Link>
      </div>
      {!validJobId(id) ? (
        <div role="alert" className="error">
          {ru.invalidLink}
        </div>
      ) : q.error instanceof HttpError && q.error.status === 401 ? (
        <KeyGate />
      ) : q.isError ? (
        <div role="alert" className="error">
          {q.error.message}
        </div>
      ) : !q.data ? (
        <div className="skeleton" role="status" aria-label={ru.loading} />
      ) : (
        <>
          <p className="mono muted">{id.slice(0, 8)}</p>
          {q.data.status === "queued" || q.data.status === "running" ? (
            <StageStepper job={q.data} />
          ) : (
            <div className="panel" role="status">
              {q.data.status === "failed"
                ? q.data.error?.message_ru || ru.failed
                : q.data.verdict === "violation"
                  ? ru.violation
                  : ru.clean}
            </div>
          )}
        </>
      )}
    </section>
  );
}
