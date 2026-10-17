import type { JobView } from "../../shared/api/decode";
import { ru } from "../../shared/i18n/ru";
import { timecode } from "../../shared/lib/format";
import { EvidenceStrip } from "../evidence/EvidenceStrip";
export function SignalList({
  items,
  onSeek,
}: {
  items: JobView["signals"];
  onSeek: (time: number) => void;
}) {
  if (!items.length) return null;
  return (
    <details className="signals panel">
      <summary>
        {ru.signals} · {items.length}
      </summary>
      <p className="muted">{ru.signalsNote}</p>
      {items.map((s) => (
        <article key={s.id || `${s.title_ru}-${s.t_sec}`}>
          <h3>{s.title_ru}</h3>
          <button type="button" onClick={() => onSeek(s.t_sec)}>
            {timecode(s.t_sec)}
          </button>
          <p>{s.description_ru}</p>
          <EvidenceStrip items={s.evidence} title={s.title_ru} onSeek={onSeek} />
        </article>
      ))}
    </details>
  );
}
