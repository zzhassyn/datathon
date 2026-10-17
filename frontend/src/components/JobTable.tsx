import { ChevronRight, Video, Search } from "lucide-react";
import type { AnalysisJob } from "../types";
import { dateLabel, sizeLabel } from "../lib/format";
import { StatusBadge } from "./StatusBadge";
export function JobTable({
  jobs,
  onOpen,
  search,
  setSearch,
}: {
  jobs: AnalysisJob[];
  onOpen: (id: string) => void;
  search?: string;
  setSearch?: (s: string) => void;
}) {
  return (
    <div className="table-panel">
      {setSearch && (
        <div className="table-tools">
          <div className="search-box">
            <Search size={16} />
            <input
              aria-label="Поиск по имени видео"
              placeholder="Найти видеозапись…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <span>{jobs.length} записей</span>
        </div>
      )}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Видеозапись</th>
              <th>Дата загрузки</th>
              <th>Статус</th>
              <th>События</th>
              <th>Источник</th>
              <th>
                <span className="sr-only">Открыть</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id} onClick={() => onOpen(job.id)}>
                <td>
                  <button
                    className="file-cell"
                    onClick={(e) => {
                      e.stopPropagation();
                      onOpen(job.id);
                    }}
                  >
                    <span className="file-icon">
                      <Video size={18} />
                    </span>
                    <span>
                      <strong>{job.video.name}</strong>
                      <small>{sizeLabel(job.video.size)}</small>
                    </span>
                  </button>
                </td>
                <td className="muted">{dateLabel(job.createdAt)}</td>
                <td>
                  <StatusBadge status={job.status} />
                </td>
                <td>
                  {job.result ? (
                    <span
                      className={
                        job.result.violations.length ? "event-count" : "muted"
                      }
                    >
                      {job.result.violations.length
                        ? `${job.result.violations.length} на проверку`
                        : "Не обнаружены"}
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
                <td>
                  <span className={`source-tag ${job.mode}`}>
                    {job.mode === "mock" ? "DEMO" : "LOCAL API"}
                  </span>
                </td>
                <td>
                  <button
                    className="icon-button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onOpen(job.id);
                    }}
                    aria-label={`Открыть ${job.video.name}`}
                  >
                    <ChevronRight size={16} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!jobs.length && (
          <div className="empty-state">
            <Search size={30} />
            <h3>Записи не найдены</h3>
            <p>Измените поиск или загрузите новое видео.</p>
          </div>
        )}
      </div>
    </div>
  );
}
