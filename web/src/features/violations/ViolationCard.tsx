import { Clock3, Play } from "lucide-react";
import type { EventView } from "../../shared/api/decode";
import { ru } from "../../shared/i18n/ru";
import { number, timecode } from "../../shared/lib/format";
import { EvidenceStrip } from "../evidence/EvidenceStrip";
import { LegalReference } from "./LegalReference";
export function ViolationCard({
  event,
  index,
  provisional,
  selected,
  onSeek,
}: {
  event: EventView;
  index: number;
  provisional: boolean;
  selected: boolean;
  onSeek: (time: number, context?: boolean) => void;
}) {
  return (
    <article id={`event-${index}`} className={`event-card ${selected ? "selected" : ""}`}>
      <header className="event-card-head">
        <span className="event-number">{index + 1}</span>
        <h3>{event.title_ru}</h3>
        <button
          type="button"
          className="timecode-button mono"
          onClick={() => onSeek(event.timecode_sec)}
        >
          <Clock3 size={14} />
          {timecode(event.timecode_sec)}
        </button>
      </header>
      <div className="event-content">
        <div className="event-description">
          <div className="actions">
            {(provisional || event.provisional) && <span className="badge">{ru.provisional}</span>}
            {event.vlm_status && (
              <span className="badge">{ru.vlm[event.vlm_status] || event.vlm_status}</span>
            )}
          </div>
          <h4 className="eyebrow">{ru.what}</h4>
          <p>{event.description_ru}</p>
          <button
            type="button"
            className="context-button"
            onClick={() => onSeek(event.timecode_sec, true)}
          >
            <Play size={14} />
            {ru.context}
            <span className="muted">{ru.contextDuration}</span>
          </button>
        </div>
        <div className="event-proof">
          <h4 className="eyebrow">{ru.evidence}</h4>
          <EvidenceStrip items={event.evidence} title={event.title_ru} onSeek={onSeek} />
          {event.legal_note_ru && <p className="muted legal-note">{event.legal_note_ru}</p>}
          <LegalReference items={event.legal} />
          {(event.code || event.confidence !== null) && (
            <details className="technical">
              <summary>{ru.technical}</summary>
              {event.code && <p className="mono">{event.code}</p>}
              {event.confidence !== null && (
                <p>
                  {ru.modelScore}: {number(event.confidence)}
                </p>
              )}
            </details>
          )}
        </div>
      </div>
    </article>
  );
}
