import Lightbox from "yet-another-react-lightbox";
import Captions from "yet-another-react-lightbox/plugins/captions";
import Zoom from "yet-another-react-lightbox/plugins/zoom";
import "yet-another-react-lightbox/styles.css";
import "yet-another-react-lightbox/plugins/captions.css";
import type { EvidenceView } from "../../shared/api/decode";
import { ru } from "../../shared/i18n/ru";
import { timecode } from "../../shared/lib/format";
export function EvidenceLightbox({
  items,
  index,
  title,
  onClose,
  onSeek,
}: {
  items: EvidenceView[];
  index: number;
  title: string;
  onClose: () => void;
  onSeek: (time: number) => void;
}) {
  return (
    <Lightbox
      open
      index={index}
      close={onClose}
      plugins={[Zoom, Captions]}
      slides={items.map((frame) => ({
        src: frame.url,
        width: frame.width || undefined,
        height: frame.height || undefined,
        alt: `${title} · ${timecode(frame.timestamp_sec)}`,
        title: `${title} · ${timecode(frame.timestamp_sec)}`,
      }))}
      labels={{
        Close: ru.close,
        Next: ru.next,
        Previous: ru.previous,
        "Zoom in": ru.zoomIn,
        "Zoom out": ru.zoomOut,
      }}
      render={{
        slideFooter: ({ slide }) => {
          const frame = items.find((f) => f.url === slide.src);
          return frame ? (
            <button
              className="lightbox-seek"
              type="button"
              onClick={() => {
                onClose();
                onSeek(frame.timestamp_sec);
              }}
            >
              {ru.playAt}
            </button>
          ) : null;
        },
      }}
    />
  );
}
