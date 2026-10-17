import { CheckCircle2, CircleHelp, CircleX, ListChecks, Play } from "lucide-react";
import type { EvidenceView, ExplanationView } from "../../shared/api/decode";
import { ru } from "../../shared/i18n/ru";
import { timecode } from "../../shared/lib/format";

export function RuleExplanation({
  explanation,
  evidence,
  onSeek,
}: {
  explanation: ExplanationView | null;
  evidence: EvidenceView[];
  onSeek: (time: number) => void;
}) {
  return (
    <details className="diagnostic-panel rule-explanation">
      <summary>
        <ListChecks size={16} aria-hidden="true" />
        <span>{ru.whyTriggered}</span>
        {!explanation && <span className="diagnostic-summary-note">{ru.noData}</span>}
      </summary>
      {explanation ? (
        <div className="diagnostic-body">
          <p className="muted diagnostic-note">{ru.explanationNote}</p>
          <ol className="diagnostic-list">
            {explanation.conditions.map((condition) => {
              const Icon =
                condition.status === "met"
                  ? CheckCircle2
                  : condition.status === "not_met"
                    ? CircleX
                    : CircleHelp;
              return (
                <li key={condition.id} className={`diagnostic-item condition-${condition.status}`}>
                  <Icon size={17} className="diagnostic-icon" aria-hidden="true" />
                  <div>
                    <div className="diagnostic-item-head">
                      <span>{condition.text_ru}</span>
                      <span className="badge diagnostic-status">
                        {ru.conditionStatus[condition.status]}
                      </span>
                    </div>
                    <p className="muted">{condition.reason_ru}</p>
                    <div className="diagnostic-actions">
                      {(condition.evidence_indices ?? []).map((index) => {
                        const frame = evidence[index];
                        return frame ? (
                          <button
                            key={index}
                            type="button"
                            className="quiet mono"
                            aria-label={`${ru.showMoment}: ${condition.text_ru}, ${timecode(frame.timestamp_sec)}`}
                            onClick={() => onSeek(frame.timestamp_sec)}
                          >
                            <Play size={12} aria-hidden="true" />
                            {ru.frame} {index + 1} · {timecode(frame.timestamp_sec)}
                          </button>
                        ) : null;
                      })}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
          <p className="diagnostic-version muted">
            {ru.ruleVersion}: {explanation.rule_version}
          </p>
        </div>
      ) : (
        <p className="diagnostic-body muted">{ru.explanationMissing}</p>
      )}
    </details>
  );
}
