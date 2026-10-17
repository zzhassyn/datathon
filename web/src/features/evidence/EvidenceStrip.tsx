import { ZoomIn } from "lucide-react";
import { lazy, Suspense, useRef, useState } from "react";
import type { EvidenceView } from "../../shared/api/decode";
import { ru } from "../../shared/i18n/ru";
import { timecode } from "../../shared/lib/format";

const load = () => import("./EvidenceLightbox");
const EvidenceLightbox = lazy(() => load().then((m) => ({ default: m.EvidenceLightbox })));
export function EvidenceStrip({
  items,
  title,
  onSeek,
}: {
  items: EvidenceView[];
  title: string;
  onSeek: (time: number) => void;
}) {
  const [selected, setSelected] = useState<number | null>(null);
  const [failed, setFailed] = useState<string[]>([]);
  const trigger = useRef<HTMLButtonElement | null>(null);
  if (!items.length) return <p className="muted evidence-missing">{ru.noEvidence}</p>;
  return (
    <>
      <fieldset className="evidence-grid" aria-label={ru.evidence}>
        {items.map((frame, index) => (
          <button
            key={frame.url}
            type="button"
            className="evidence"
            aria-label={`${ru.zoomIn}: ${title}, ${timecode(frame.timestamp_sec)}`}
            onMouseEnter={() => void load()}
            onFocus={() => void load()}
            onClick={(e) => {
              trigger.current = e.currentTarget;
              setSelected(index);
            }}
            disabled={failed.includes(frame.url)}
          >
            {failed.includes(frame.url) ? (
              <span>{ru.frameMissing}</span>
            ) : (
              <img
                src={frame.url}
                alt={`${frame.kind === "zoom" ? ru.zoom : frame.kind === "context" ? ru.before : ru.frame}, ${timecode(frame.timestamp_sec)}: ${title}`}
                width={frame.width || 160}
                height={frame.height || 120}
                loading="lazy"
                decoding="async"
                onError={() => setFailed((current) => [...current, frame.url])}
              />
            )}
            <span className="evidence-time mono">{timecode(frame.timestamp_sec)}</span>
            <ZoomIn className="evidence-zoom" size={15} />
          </button>
        ))}
      </fieldset>
      {selected !== null && (
        <Suspense fallback={<p role="status">{ru.loading}</p>}>
          <EvidenceLightbox
            items={items}
            title={title}
            index={selected}
            onSeek={onSeek}
            onClose={() => {
              setSelected(null);
              requestAnimationFrame(() => trigger.current?.focus({ preventScroll: true }));
            }}
          />
        </Suspense>
      )}
    </>
  );
}
