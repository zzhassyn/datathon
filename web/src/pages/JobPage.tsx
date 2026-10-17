import { Copy, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Marker } from "../features/player/markers";
import { type PlayerHandle, VideoPlayer } from "../features/player/VideoPlayer";
import { StageStepper } from "../features/progress/StageStepper";
import { readRecent, saveRecent } from "../features/recent/recentStore";
import { SignalList } from "../features/signals/SignalList";
import { clearPreview, getPreview } from "../features/upload/previewStore";
import { VerdictBanner } from "../features/verdict/VerdictBanner";
import { ViolationCard } from "../features/violations/ViolationCard";
import { HttpError } from "../shared/api/errors";
import { active, useJob } from "../shared/api/queries";
import { ru } from "../shared/i18n/ru";
import { timecode } from "../shared/lib/format";
import { validJobId } from "../shared/lib/urlState";
import { KeyGate } from "../shared/ui/KeyGate";
import { Link } from "../shared/ui/Link";
export function JobPage({ id }: { id: string }) {
  const q = useJob(id);
  const player = useRef<PlayerHandle>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [notice, setNotice] = useState("");
  const [localUrl] = useState(() => getPreview(id));
  const job = q.data;
  useEffect(() => () => clearPreview(id), [id]);
  useEffect(() => {
    if (!job) return;
    document.title = `${job.status === "done" ? ru.done : job.status === "failed" ? ru.failed : ru.running} — ${ru.brand}`;
    const previous = readRecent().find((r) => r.id === id);
    if (previous)
      saveRecent({
        ...previous,
        lastStatus: job.status,
        verdict: job.verdict,
        nViolations: job.violations.length,
      });
  }, [id, job]);
  const markers: Marker[] =
    job && job.status !== "failed"
      ? [
          ...job.violations.map((v, i) => ({
            id: v.id || `v-${i}`,
            time: v.timecode_sec,
            title: v.title_ru,
            index: i,
            provisional: job.provisional || v.provisional,
          })),
          ...job.signals.map((s, i) => ({
            id: s.id || `s-${i}`,
            time: s.t_sec,
            title: s.title_ru,
            index: i,
            signal: true,
          })),
        ]
      : [];
  function seek(time: number, context = false, index?: number) {
    if (index !== undefined) setSelected(index);
    player.current?.seek(time, context);
  }
  return (
    <section className="page job-page">
      <div className="section-heading">
        <div>
          <h1 tabIndex={-1}>{ru.jobTitle}</h1>
          {job && (
            <p className="job-meta">
              <span className="mono">{id.slice(0, 8)}</span>
              <span>{new Date(job.created_at).toLocaleString("ru-RU")}</span>
              <span>
                {ru.elapsed} {timecode(job.elapsed_sec)}
              </span>
            </p>
          )}
        </div>
        <div className="actions">
          <button
            type="button"
            className="icon-button"
            aria-label={ru.copy}
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(`${location.origin}/?job=${id}`);
                setNotice(ru.copied);
              } catch {
                setNotice(ru.copyError);
              }
            }}
          >
            <Copy size={16} />
          </button>
          <Link className="button-link" href="/">
            <Plus size={16} />
            {ru.newJob}
          </Link>
        </div>
      </div>
      {notice && (
        <p role="status" className="muted">
          {notice}
        </p>
      )}
      {!validJobId(id) ? (
        <div role="alert" className="error">
          {ru.invalidLink}
        </div>
      ) : q.error instanceof HttpError && q.error.status === 401 ? (
        <KeyGate />
      ) : q.error instanceof HttpError && q.error.status === 404 ? (
        <div role="alert" className="error">
          {ru.notFound}
        </div>
      ) : (
        <>
          {q.isError && (
            <div role="alert" className="error">
              <p>{job ? ru.reconnecting : q.error.message}</p>
              <button type="button" onClick={() => void q.refetch()}>
                {ru.retry}
              </button>
            </div>
          )}
          {!job && !q.isError ? (
            <div className="skeleton" role="status" aria-label={ru.loading} />
          ) : (
            job && (
              <>
                {job.status === "failed" ? (
                  <div className="error" role="alert">
                    <h2>{ru.failed}</h2>
                    <p>{job.error?.message_ru || ru.invalidResponse}</p>
                  </div>
                ) : (
                  <>
                    <VerdictBanner job={job} />
                    {active(job.status) && <StageStepper job={job} />}
                  </>
                )}
                {!active(job.status) && !["done", "failed"].includes(job.status) && (
                  <p className="error">
                    {ru.unknown}: {job.status}
                  </p>
                )}
                <VideoPlayer
                  ref={player}
                  serverUrl={job.video?.url || null}
                  localUrl={localUrl}
                  duration={job.video?.duration_sec || 0}
                  markers={markers}
                  finished={job.status === "done" || job.status === "failed"}
                  onSelect={(m) => seek(m.time, false, m.signal ? undefined : m.index)}
                />
                {job.status !== "failed" && (
                  <>
                    <section className="events">
                      <div className="section-heading">
                        <h2>
                          {ru.events} <span className="badge">{job.violations.length}</span>
                        </h2>
                        {job.provisional && <span className="badge">{ru.provisional}</span>}
                      </div>
                      <div className="event-list">
                        {job.violations.map((event, i) => (
                          <ViolationCard
                            key={event.id || `${event.timecode_sec}-${event.title_ru}`}
                            event={event}
                            index={i}
                            selected={selected === i}
                            provisional={job.provisional}
                            onSeek={(t, context) => seek(t, context, i)}
                          />
                        ))}
                      </div>
                    </section>
                    <SignalList items={job.signals} onSeek={(t) => seek(t)} />
                  </>
                )}
                <details className="technical">
                  <summary>{ru.technical}</summary>
                  <p className="mono">{job.job_id}</p>
                  <p>
                    {job.perception_level} · {job.result_level}
                  </p>
                </details>
              </>
            )
          )}
        </>
      )}
    </section>
  );
}
