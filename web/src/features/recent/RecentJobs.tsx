import { FileVideo, X } from "lucide-react";
import { useState } from "react";
import { ru } from "../../shared/i18n/ru";
import { Link } from "../../shared/ui/Link";
import { readRecent, removeRecent } from "./recentStore";
export function RecentJobs() {
  const [rows, setRows] = useState(readRecent);

  return (
    <section className="recent">
      <div className="section-heading">
        <h2>{ru.recent}</h2>
        {rows.length > 0 && (
          <button
            type="button"
            className="quiet"
            onClick={() => {
              removeRecent();
              setRows([]);
            }}
          >
            {ru.clearRecent}
          </button>
        )}
      </div>
      {!rows.length && (
        <div className="recent-empty">
          <FileVideo size={22} />
          <div>
            <p>{ru.previousChecks}</p>
            <span className="muted">{ru.previousChecksHint}</span>
          </div>
        </div>
      )}
      {rows.map((row) => (
        <div key={row.id} className="recent-row">
          <FileVideo size={18} />
          <Link href={`/?job=${row.id}`}>
            <strong>{row.fileName || ru.noName}</strong>
            <span className="muted">{new Date(row.createdAt).toLocaleString("ru-RU")}</span>
          </Link>
          <span className="badge">
            {row.lastStatus === "done"
              ? row.verdict === "violation"
                ? ru.violation
                : ru.clean
              : row.lastStatus === "failed"
                ? ru.failed
                : ru.running}
          </span>
          <button
            type="button"
            className="icon-button"
            aria-label={`${ru.remove}: ${row.fileName}`}
            onClick={() => {
              removeRecent(row.id);
              setRows(readRecent());
            }}
          >
            <X size={15} />
          </button>
        </div>
      ))}
    </section>
  );
}
