import {
  ArrowRight,
  ArrowUpRight,
  Video,
  ScanLine,
  Clock3,
  ShieldCheck,
  Play,
  Plus,
  CircleDot,
} from "lucide-react";
import type { AnalysisJob } from "../types";
import { isActive, timecode } from "../lib/format";
import { JobTable } from "../components/JobTable";
export function Dashboard({
  jobs,
  onOpen,
  onUpload,
  onHistory,
}: {
  jobs: AnalysisJob[];
  onOpen: (id: string) => void;
  onUpload: () => void;
  onHistory: () => void;
}) {
  const completed = jobs.filter((j) => j.status === "done");
  const events = completed
    .flatMap((j) => j.result?.violations ?? [])
    .filter((v) => v.review === "pending").length;
  const active = jobs.filter((j) => isActive(j.status)).length;
  const reviewed = completed
    .flatMap((j) => j.result?.violations ?? [])
    .filter((v) => v.review !== "pending").length;
  const featured = completed.find((j) => j.result?.violations.length);
  const demo = jobs.every((j) => j.mode === "mock");
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО</div>
          <h1>Обзор аналитики</h1>
          <p>Внимание к деталям. Уверенность в каждом решении.</p>
        </div>
        <button className="button primary" onClick={onUpload}>
          <Plus size={18} />
          Новый анализ
        </button>
      </div>
      <div className="overview-note">
        <span className="live-dot" />
        {demo
          ? "Демонстрационное пространство"
          : "Локальное рабочее пространство"}
        <span className="note-divider" />
        <span>
          {demo
            ? "Синтетические записи и сценарии · AI-модель не подключена"
            : "Результаты сгруппированы по источнику: DEMO / LOCAL API"}
        </span>
      </div>
      <section className="metrics-grid" aria-label="Статистика">
        <Metric
          title="Обработано видео"
          value={completed.length}
          Icon={Video}
          caption="Завершённые анализы"
        />
        <Metric
          title="Событий на проверку"
          value={events}
          Icon={ScanLine}
          caption="Признаки возможных нарушений"
          accent
        />
        <Metric
          title="В обработке"
          value={active}
          Icon={Clock3}
          caption={active ? "Проверяем состояние задач" : "Очередь свободна"}
        />
        <Metric
          title="Проверено человеком"
          value={reviewed}
          Icon={ShieldCheck}
          caption="Решения по отдельным событиям"
        />
      </section>
      <section className="feature-grid">
        <div className="featured-panel">
          <div className="panel-heading">
            <div>
              <h2>От события к доказательству</h2>
              <p>Контекст, точный момент и объяснение — в одном окне.</p>
            </div>
            <span className="tiny-label">ВОЗМОЖНОСТИ ПЛАТФОРМЫ</span>
          </div>
          <div className="feature-content">
            <button
              className="camera-preview"
              onClick={() => (featured ? onOpen(featured.id) : onUpload())}
              aria-label="Открыть демонстрационный результат"
            >
              <img
                src="/demo/cash-desk.svg"
                alt="Синтетическая иллюстрация кассового помещения"
              />
              <span className="camera-top">
                <span>
                  <CircleDot size={12} />
                  CAM 01 · Кассовая зона
                </span>
                <span>DEMO</span>
              </span>
              <span className="preview-box">
                <span>Область внимания · демо</span>
              </span>
              <span className="play-circle">
                <Play size={23} fill="currentColor" />
              </span>
              <span className="camera-bottom">
                Синтетическая запись<span>00:08 / 00:24</span>
              </span>
            </button>
            <div className="feature-copy">
              <span className="pill light-burgundy">
                <ScanLine size={13} />
                Human-in-the-loop
              </span>
              <h3>
                AI замечает.
                <br />
                Человек решает.
              </h3>
              <p>
                Просматривайте кадры, сравнивайте оригинал с аннотациями и
                проверяйте каждое предполагаемое нарушение.
              </p>
              <button
                className="text-button"
                onClick={() => (featured ? onOpen(featured.id) : onUpload())}
              >
                Открыть демосценарий
                <ArrowUpRight size={16} />
              </button>
            </div>
          </div>
        </div>
        <aside className="flow-panel">
          <div className="panel-heading">
            <h2>Как это работает</h2>
            <span className="pill">3 шага</span>
          </div>
          <div className="flow-step">
            <span>01</span>
            <div>
              <h3>Загрузите запись</h3>
              <p>
                MOV, MP4 или WebM.
                <br />
                Просмотр прямо в браузере.
              </p>
            </div>
          </div>
          <div className="flow-step">
            <span>02</span>
            <div>
              <h3>Запустите анализ</h3>
              <p>
                Отправка в локальный API
                <br />
                или демонстрация сценария.
              </p>
            </div>
          </div>
          <div className="flow-step">
            <span>03</span>
            <div>
              <h3>Проверьте события</h3>
              <p>
                Кадры, таймкоды и пояснения.
                <br />
                Решение остаётся за вами.
              </p>
            </div>
          </div>
          <div className="privacy-note">
            <ShieldCheck size={18} />
            <span>
              Локальная обработка
              <br />
              <small>Без распознавания личности</small>
            </span>
          </div>
        </aside>
      </section>
      <section className="history-section">
        <div className="section-heading">
          <div>
            <h2>
              Последние анализы{" "}
              <span className="count-label">{jobs.length}</span>
            </h2>
            <p>Все записи и результаты вашего рабочего пространства.</p>
          </div>
          <button className="text-button subtle" onClick={onHistory}>
            Вся история
            <ArrowRight size={16} />
          </button>
        </div>
        <JobTable jobs={jobs.slice(0, 4)} onOpen={onOpen} />
      </section>
      <div className="bottom-caption">
        <ShieldCheck size={14} />
        <span>
          Результаты AI — основание для проверки, а не подтверждение нарушения.
        </span>
        <span className="last-caption">FORTE VISION · DATATHON 26</span>
      </div>
    </>
  );
}
function Metric({
  title,
  value,
  Icon,
  caption,
  accent,
}: {
  title: string;
  value: number;
  Icon: typeof Video;
  caption: string;
  accent?: boolean;
}) {
  return (
    <article className={`metric-card ${accent ? "metric-accent" : ""}`}>
      <div>
        <span>{title}</span>
        <span className="metric-icon">
          <Icon size={18} />
        </span>
      </div>
      <strong>{String(value).padStart(2, "0")}</strong>
      <p>{caption}</p>
    </article>
  );
}
export function RecentEventLabel({ job }: { job: AnalysisJob }) {
  return <span>{timecode(job.result?.violations[0]?.timecode_sec ?? 0)}</span>;
}
