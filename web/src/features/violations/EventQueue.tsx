import { CheckCircle2, Clock3, Image } from "lucide-react";
import type { JobView } from "../../shared/api/decode";
import { active } from "../../shared/api/queries";
import { ru } from "../../shared/i18n/ru";
import { timecode } from "../../shared/lib/format";
import { StageStepper } from "../progress/StageStepper";

export function EventQueue({
  job,
  selected,
  onSelect,
}: {
  job: JobView;
  selected: number;
  onSelect: (index: number) => void;
}) {
  const pending = active(job.status);
  return (
    <aside className="review-sidebar" aria-label={ru.review}>
      <div className="review-heading">
        <h2>{ru.events}</h2>
        <span className="badge">{job.violations.length}</span>
      </div>
      {pending && <StageStepper job={job} />}
      {job.violations.length > 0 ? (
        <div className="event-queue">
          {job.violations.map((event, index) => (
            <button
              type="button"
              className={`event-choice ${selected === index ? "selected" : ""}`}
              key={event.id || `${event.timecode_sec}-${event.title_ru}`}
              aria-pressed={selected === index}
              aria-controls="selected-event"
              onClick={() => onSelect(index)}
            >
              <span className="event-thumbnail">
                {event.evidence[0] ? (
                  <img
                    src={event.evidence[0].url}
                    alt=""
                    loading="lazy"
                    onError={(e) => {
                      e.currentTarget.hidden = true;
                    }}
                  />
                ) : (
                  <Image size={20} />
                )}
                <span className="mono">{timecode(event.timecode_sec)}</span>
              </span>
              <span className="event-choice-copy">
                <span className="eyebrow">
                  {ru.moment} {String(index + 1).padStart(2, "0")}
                </span>
                <strong>{event.title_ru}</strong>
                <span className="muted">
                  {ru.framesCount}: {event.evidence.length}
                </span>
                {(job.provisional || event.provisional) && (
                  <span className="badge">{ru.provisional}</span>
                )}
              </span>
            </button>
          ))}
        </div>
      ) : (
        <div className="review-empty">
          {job.status === "done" ? <CheckCircle2 size={28} /> : <Clock3 size={28} />}
          <h3>{job.status === "done" ? ru.noEvents : ru.waitingEvents}</h3>
          <p>{job.status === "done" ? ru.noEventsHint : ru.waitingEventsHint}</p>
        </div>
      )}
      <dl className="video-facts">
        <div>
          <dt>{ru.duration}</dt>
          <dd>{job.video?.duration_sec ? timecode(job.video.duration_sec) : ru.notAvailable}</dd>
        </div>
        <div>
          <dt>{ru.resolution}</dt>
          <dd>
            {job.video?.width && job.video.height
              ? `${job.video.width} × ${job.video.height}`
              : ru.notAvailable}
          </dd>
        </div>
        <div>
          <dt>{ru.elapsed}</dt>
          <dd>{timecode(job.elapsed_sec)}</dd>
        </div>
      </dl>
    </aside>
  );
}
