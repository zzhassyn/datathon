import { useState } from "react";
import { Plus, SlidersHorizontal } from "lucide-react";
import type { AnalysisJob } from "../types";
import { JobTable } from "../components/JobTable";
export function History({
  jobs,
  onOpen,
  onUpload,
}: {
  jobs: AnalysisJob[];
  onOpen: (id: string) => void;
  onUpload: () => void;
}) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const filtered = jobs.filter(
    (j) =>
      j.video.name.toLowerCase().includes(search.toLowerCase()) &&
      (filter === "all" || j.status === filter),
  );
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">АРХИВ РАБОЧЕГО ПРОСТРАНСТВА</div>
          <h1>История анализов</h1>
          <p>Возвращайтесь к записям и продолжайте проверку событий.</p>
        </div>
        <button className="button primary" onClick={onUpload}>
          <Plus size={18} />
          Новый анализ
        </button>
      </div>
      <div className="filter-bar">
        <div className="tabs">
          {[
            ["all", "Все записи"],
            ["done", "Завершённые"],
            ["processing", "В анализе"],
            ["failed", "С ошибкой"],
          ].map(([value, label]) => (
            <button
              key={value}
              className={filter === value ? "active" : ""}
              onClick={() => setFilter(value)}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="muted filter-label">
          <SlidersHorizontal size={15} />
          По дате загрузки
        </span>
      </div>
      <JobTable
        jobs={filtered}
        onOpen={onOpen}
        search={search}
        setSearch={setSearch}
      />
      <p className="storage-note">
        История хранится в этом браузере. После перезагрузки пользовательское
        видео и кадры нужно выбрать повторно. Синтетические демозаписи доступны
        всегда.
      </p>
    </>
  );
}
