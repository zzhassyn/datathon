import { useEffect, useRef, useState } from "react";
import { ru } from "../../shared/i18n/ru";
import { timecode } from "../../shared/lib/format";
import { clusterMarkers, type Marker, markerPercent } from "./markers";
export function EventTimeline({
  markers,
  duration,
  time,
  onSelect,
}: {
  markers: Marker[];
  duration: number;
  time: number;
  onSelect: (m: Marker) => void;
}) {
  const element = useRef<HTMLFieldSetElement>(null);
  const [width, setWidth] = useState(900);
  const [opened, setOpened] = useState<string | null>(null);
  useEffect(() => {
    if (!element.current) return;
    const observer = new ResizeObserver((entries) => setWidth(entries[0].contentRect.width));
    observer.observe(element.current);
    return () => observer.disconnect();
  }, []);
  if (duration <= 0) return null;
  return (
    <fieldset className="timeline" ref={element} aria-label={ru.timeline}>
      <div className="timeline-heading">
        <span>{ru.timeline}</span>
        <span className="mono">
          {timecode(time)} / {timecode(duration)}
        </span>
      </div>
      <div className="timeline-track">
        <span className="playhead" style={{ left: `${markerPercent(time, duration)}%` }} />
        {clusterMarkers(markers, duration, width).map((group) => {
          const first = group.items[0];
          return (
            <div key={first.id} className="marker-position" style={{ left: `${group.position}%` }}>
              <button
                type="button"
                className={`marker ${first.signal ? "signal" : ""} ${first.provisional ? "provisional" : ""}`}
                aria-label={`${group.items.length > 1 ? `${ru.events}: ${group.items.length}` : first.title}, ${timecode(first.time)}`}
                aria-expanded={group.items.length > 1 ? opened === first.id : undefined}
                onClick={() =>
                  group.items.length === 1
                    ? onSelect(first)
                    : setOpened(opened === first.id ? null : first.id)
                }
              >
                {group.items.length > 1 ? `+${group.items.length}` : first.index + 1}
              </button>
              {opened === first.id && group.items.length > 1 && (
                <div className="marker-menu">
                  {group.items.map((item) => (
                    <button
                      type="button"
                      key={item.id}
                      onClick={() => {
                        onSelect(item);
                        setOpened(null);
                      }}
                    >
                      <span className="mono">{timecode(item.time)}</span>
                      {item.title}
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="timeline-labels">
        {[0, 0.25, 0.5, 0.75, 1].map((fraction) => (
          <span key={fraction}>{timecode(duration * fraction)}</span>
        ))}
      </div>
    </fieldset>
  );
}
