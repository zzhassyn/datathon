import { ArrowUpRight, BookOpen } from "lucide-react";
import type { LegalView } from "../../shared/api/decode";
import { ru } from "../../shared/i18n/ru";
export function LegalReference({ items }: { items: LegalView[] }) {
  return (
    <details className="legal-list">
      <summary>
        <BookOpen size={15} />
        {ru.legal}
        {!items.length && <span className="muted"> · {ru.noLegal}</span>}
      </summary>
      {items.map((item) => (
        <div className="legal-item" key={`${item.act_title_ru}-${item.clause}-${item.quote_ru}`}>
          <p>{item.act_title_ru}</p>
          {item.clause && <p>{item.clause}</p>}
          {item.strength && (
            <span className="badge">
              {item.strength === "direct"
                ? ru.direct
                : item.strength === "indirect"
                  ? ru.indirect
                  : item.strength === "none"
                    ? ru.none
                    : item.strength}
            </span>
          )}
          {item.quote_ru && <blockquote>{item.quote_ru}</blockquote>}
          {item.note_ru && <p className="muted">{item.note_ru}</p>}
          {item.url && (
            <>
              <a href={item.url} target="_blank" rel="noopener noreferrer">
                {ru.openSource}
                <ArrowUpRight size={14} />
              </a>
              <p className="muted">{ru.externalNote}</p>
            </>
          )}
        </div>
      ))}
    </details>
  );
}
