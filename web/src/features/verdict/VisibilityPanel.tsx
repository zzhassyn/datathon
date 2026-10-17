import { Eye, Play } from "lucide-react";
import type { VisibilityView } from "../../shared/api/decode";
import { ru } from "../../shared/i18n/ru";
import { timecode } from "../../shared/lib/format";

export function VisibilityPanel({
  assessment,
  provisional,
  onSeek,
}: {
  assessment: VisibilityView | null;
  provisional: boolean;
  onSeek: (time: number) => void;
}) {
  return (
    <details className="diagnostic-panel visibility-panel">
      <summary>
        <Eye size={16} aria-hidden="true" />
        <span>{ru.visibilityTitle}</span>
        {provisional && assessment ? <span className="badge">{ru.provisional}</span> : null}
        {!assessment && <span className="diagnostic-summary-note">{ru.noData}</span>}
      </summary>
      {assessment ? (
        <div className="diagnostic-body">
          <p className="muted diagnostic-note">{ru.visibilityNote}</p>
          <ul className="diagnostic-list">
            {assessment.checks.map((check) => (
              <li key={check.id} className={`diagnostic-item visibility-${check.status}`}>
                <span className="visibility-dot" aria-hidden="true" />
                <div>
                  <div className="diagnostic-item-head">
                    <span>{check.target_ru}</span>
                    <span className="badge diagnostic-status">
                      {ru.visibilityStatus[check.status]}
                    </span>
                  </div>
                  <p className="muted">{check.reason_ru}</p>
                  {check.start_sec != null && check.end_sec != null ? (
                    <button
                      type="button"
                      className="quiet mono"
                      aria-label={`${ru.showWindow}: ${check.target_ru}, ${timecode(check.start_sec)} — ${timecode(check.end_sec)}`}
                      onClick={() => {
                        if (check.start_sec != null) onSeek(check.start_sec);
                      }}
                    >
                      <Play size={12} aria-hidden="true" />
                      {timecode(check.start_sec)} — {timecode(check.end_sec)}
                    </button>
                  ) : (
                    <span className="muted diagnostic-time-missing">{ru.noMoment}</span>
                  )}
                </div>
              </li>
            ))}
          </ul>
          <p className="diagnostic-version muted">
            {ru.visibilityMethod}: {assessment.method_version}
          </p>
        </div>
      ) : (
        <p className="diagnostic-body muted">{ru.visibilityMissing}</p>
      )}
    </details>
  );
}
