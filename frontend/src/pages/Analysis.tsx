import { useEffect, useState } from "react";
import {
  ArrowLeft,
  Check,
  CheckCheck,
  ChevronRight,
  CircleAlert,
  Clock3,
  ExternalLink,
  Film,
  Focus,
  LoaderCircle,
  Play,
  RotateCcw,
  ScanLine,
  ShieldCheck,
  X,
} from "lucide-react";
import type { AnalysisJob, ReviewStatus, Violation } from "../types";
import { dateLabel, isActive, sizeLabel, timecode } from "../lib/format";
import { StatusBadge } from "../components/StatusBadge";
import { VideoPlayer } from "../components/VideoPlayer";
import { captureFrame } from "../lib/media";
export function Analysis({
  job,
  onBack,
  onReview,
  onRetry,
  onReattach,
  onCancel,
  awaitingKey,
  onConfigure,
}: {
  job: AnalysisJob;
  onBack: () => void;
  onReview: (id: string, status: ReviewStatus) => void;
  onRetry: () => void;
  onReattach: () => void;
  onCancel: () => void;
  awaitingKey?: boolean;
  onConfigure: () => void;
}) {
  const [selected, setSelected] = useState("");
  const [seek, setSeek] = useState({ seconds: 0, serial: 0 });
  const events = job.result?.violations ?? [];
  const event = events.find((v) => v.id === selected) ?? events[0];
  useEffect(() => {
    setSelected("");
    setSeek({ seconds: 0, serial: 0 });
  }, [job.id]);
  function select(v: Violation) {
    setSelected(v.id);
    setSeek((s) => ({ seconds: v.timecode_sec, serial: s.serial + 1 }));
  }
  return (
    <>
      <button className="back-button" onClick={onBack}>
        <ArrowLeft size={15} />К истории анализов
      </button>
      <div className="page-heading analysis-heading">
        <div>
          <div className="eyebrow">
            {job.mode === "mock"
              ? "ДЕМОНСТРАЦИЯ · СИНТЕТИЧЕСКИЕ РЕЗУЛЬТАТЫ"
              : "РЕЗУЛЬТАТ ЛОКАЛЬНОГО API"}
          </div>
          <h1>{job.video.name}</h1>
          <p>
            {dateLabel(job.createdAt)}
            <span className="inline-divider">·</span>
            {sizeLabel(job.video.size)}
            <span className="inline-divider">·</span>
            {job.video.duration
              ? timecode(job.video.duration)
              : "Длительность не определена"}
          </p>
        </div>
        <StatusBadge status={job.status} />
      </div>
      {job.mode === "mock" && (
        <div className="demo-banner">
          <ScanLine size={18} />
          <span>
            <strong>Демонстрационный анализ.</strong> События, пояснения и рамки
            заданы для показа интерфейса.
            {!job.video.synthetic
              ? " Они не описывают содержимое загруженного видео."
              : " Запись полностью синтетическая."}{" "}
            AI-модель не запускалась.
          </span>
        </div>
      )}
      {isActive(job.status) ? (
        <div className="processing-panel">
          <div className="scan-animation">
            <ScanLine size={42} />
          </div>
          <span className="pill light-burgundy">
            {job.mode === "mock" ? "ДЕМОНСТРАЦИЯ ПРОЦЕССА" : "ЛОКАЛЬНЫЙ API"}
          </span>
          <h2>
            {awaitingKey
              ? "Ожидаем ключ локального API"
              : job.status === "uploading"
                ? "Загружаем видео"
                : job.status === "queued"
                  ? "Видео в очереди"
                  : "Проверяем видеозапись"}
          </h2>
          <p>
            {awaitingKey
              ? "Введите ключ в настройках, чтобы продолжить проверку прежнего job_id. Повторная загрузка не требуется."
              : job.mode === "mock"
                ? "Сейчас проигрывается демонстрационная последовательность статусов."
                : "Ожидаем результат от сервиса. Допустимое время — до 5 минут."}
          </p>
          {job.progress !== undefined ? (
            <>
              <div className="progress-track">
                <span style={{ width: `${job.progress}%` }} />
              </div>
              <span className="muted">{Math.round(job.progress)}%</span>
            </>
          ) : (
            <LoaderCircle className="spin" size={24} />
          )}
          <div className="processing-steps">
            {["Загрузка", "Очередь", "Анализ", "Результат"].map((s, i) => (
              <span
                key={s}
                className={
                  i <
                  ["uploading", "queued", "processing", "done"].indexOf(
                    job.status,
                  )
                    ? "complete"
                    : ""
                }
              >
                <span>
                  {i <
                  ["uploading", "queued", "processing", "done"].indexOf(
                    job.status,
                  ) ? (
                    <Check size={13} />
                  ) : (
                    i + 1
                  )}
                </span>
                {s}
              </span>
            ))}
          </div>
          {awaitingKey && (
            <button className="button primary" onClick={onConfigure}>
              Ввести ключ API
            </button>
          )}
          <button className="text-button subtle" onClick={onCancel}>
            Прекратить ожидание
          </button>
          <small>
            Остановка ожидания в браузере не отменяет задачу на сервере.
          </small>
        </div>
      ) : job.status === "failed" ? (
        <div className="empty-state error-state">
          <CircleAlert size={40} />
          <h2>Анализ не завершён</h2>
          <p>{job.error || "Сервис не смог обработать запись."}</p>
          <p className="muted">
            Ошибка обработки не означает отсутствие нарушений.
          </p>
          <button className="button primary" onClick={onRetry}>
            <RotateCcw size={16} />
            Повторить анализ
          </button>
        </div>
      ) : (
        <>
          <div
            className={`result-summary ${events.length ? "has-events" : "clean"}`}
          >
            <span className="summary-icon">
              {events.length ? <Focus size={22} /> : <ShieldCheck size={22} />}
            </span>
            <div>
              <h2>
                {events.length
                  ? "Обнаружены признаки возможных нарушений"
                  : "Нарушение не обнаружено"}
              </h2>
              <p>
                {events.length
                  ? `${events.length} события для проверки · Оцените контекст перед принятием решения`
                  : "В рамках этого результата нет событий на проверку. Это не гарантия отсутствия любых нарушений."}
              </p>
            </div>
            <span className="result-total">
              {String(events.length).padStart(2, "0")}
            </span>
          </div>
          <div className="analysis-grid">
            <div className="analysis-main">
              <VideoPlayer
                job={job}
                event={event}
                seekRequest={seek}
                onReattach={onReattach}
                onSelect={select}
              />
              {event && (
                <EvidencePanel
                  event={event}
                  job={job}
                  onSeek={() =>
                    setSeek((s) => ({
                      seconds: event.timecode_sec,
                      serial: s.serial + 1,
                    }))
                  }
                  onReview={(status) => onReview(event.id, status)}
                />
              )}
            </div>
            <aside className="events-panel">
              <div className="panel-heading">
                <h2>
                  События <span className="count-label">{events.length}</span>
                </h2>
                <ScanLine size={18} />
              </div>
              <p className="events-subtitle">Каждое событие требует проверки</p>
              <div className="events-list">
                {events.length ? (
                  events.map((v, index) => (
                    <button
                      className={`event-card ${event?.id === v.id ? "selected" : ""}`}
                      key={v.id}
                      onClick={() => select(v)}
                    >
                      <div className="event-meta">
                        <span className="event-number">
                          {String(index + 1).padStart(2, "0")}
                        </span>
                        <span>
                          <Clock3 size={12} />
                          {timecode(v.timecode_sec)}
                        </span>
                        <ChevronRight size={15} />
                      </div>
                      <h3>{v.type}</h3>
                      <p>{v.category}</p>
                      <span className={`review-label review-${v.review}`}>
                        {v.review === "confirmed" ? (
                          <CheckCheck size={12} />
                        ) : v.review === "dismissed" ? (
                          <X size={12} />
                        ) : (
                          <span className="review-dot" />
                        )}
                        {v.review === "confirmed"
                          ? "Подтверждено человеком"
                          : v.review === "dismissed"
                            ? "Отклонено человеком"
                            : "Ожидает проверки"}
                      </span>
                    </button>
                  ))
                ) : (
                  <div className="no-events">
                    <ShieldCheck size={30} />
                    <h3>Список пуст</h3>
                    <p>Сервис не вернул предполагаемых событий.</p>
                  </div>
                )}
              </div>
              <div className="review-reminder">
                <ShieldCheck size={19} />
                <strong>Решение принимает человек</strong>
                <p>
                  AI помогает найти момент. Окончательная оценка требует знания
                  контекста и правил.
                </p>
              </div>
            </aside>
          </div>
        </>
      )}
    </>
  );
}
function EvidencePanel({
  event,
  job,
  onSeek,
  onReview,
}: {
  event: Violation;
  job: AnalysisJob;
  onSeek: () => void;
  onReview: (status: ReviewStatus) => void;
}) {
  const [frameIndex, setFrameIndex] = useState(0);
  const [captured, setCaptured] = useState<string>();
  useEffect(() => {
    setFrameIndex(0);
    setCaptured(undefined);
  }, [event.id]);
  const frame = event.evidence_frames[frameIndex] ?? event.evidence_frames[0];
  useEffect(() => {
    let live = true;
    setCaptured(undefined);
    if (
      ((!frame?.image_base64 && !frame?.image_url) || frame?.synthetic) &&
      job.video.url &&
      job.video.available
    ) {
      void captureFrame(
        job.video.url,
        frame?.timestamp_sec ?? event.timecode_sec,
      ).then((value) => {
        if (live) setCaptured(value);
      });
    }
    return () => {
      live = false;
    };
  }, [frame, event.timecode_sec, job.video.url, job.video.available]);
  const image =
    frame?.synthetic && captured
      ? captured
      : frame?.image_base64
        ? frame.image_base64.startsWith("data:")
          ? frame.image_base64
          : `data:image/jpeg;base64,${frame.image_base64}`
        : (frame?.image_url ?? captured);
  return (
    <section className="evidence-panel">
      <div className="panel-heading">
        <h2>Карточка события</h2>
        <span className="pill">{timecode(event.timecode_sec)}</span>
      </div>
      <div className="evidence-layout">
        <div className="evidence-visual">
          {image ? (
            <img
              src={image}
              alt={`Кадр события: ${event.type}, ${timecode(frame?.timestamp_sec ?? event.timecode_sec)}`}
            />
          ) : (
            <div className="missing-evidence">
              <Film size={26} />
              <span>Кадр недоступен</span>
              <small>
                Выберите исходное видео повторно или запросите результат API.
              </small>
            </div>
          )}
          <span className="frame-label">
            {frame?.synthetic
              ? "СИНТЕТИЧЕСКИЙ КАДР"
              : job.mode === "mock"
                ? "КАДР ВИДЕО · СОБЫТИЕ DEMO"
                : "КАДР ИЗ API"}{" "}
            · {timecode(frame?.timestamp_sec ?? event.timecode_sec)}
          </span>
          {event.evidence_frames.length > 1 && (
            <div className="frame-buttons">
              {event.evidence_frames.map((f, i) => (
                <button
                  key={i}
                  className={frameIndex === i ? "active" : ""}
                  onClick={() => setFrameIndex(i)}
                >
                  {timecode(f.timestamp_sec)}
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="evidence-description">
          <span className="eyebrow">{event.category}</span>
          <h3>{event.type}</h3>
          <p>{event.description}</p>
          <dl>
            <div>
              <dt>Момент события</dt>
              <dd>
                <button className="text-button" onClick={onSeek}>
                  <Play size={12} />
                  {timecode(event.timecode_sec)}
                </button>
              </dd>
            </div>
            <div>
              <dt>Уверенность модели</dt>
              <dd>
                {event.confidence === undefined
                  ? "Не предоставлена"
                  : `${Math.round(event.confidence * 100)}%`}
              </dd>
            </div>
          </dl>
        </div>
      </div>
      <div className="rule-note">
        <ShieldCheck size={18} />
        <div>
          <strong>Основание для проверки</strong>
          <p>
            {event.rule?.title ??
              "Источник правила не предоставлен. Перед выводом сопоставьте событие с применимой нормой."}
            {event.rule?.note && ` ${event.rule.note}`}
          </p>
          {event.rule?.url && /^https?:\/\//.test(event.rule.url) && (
            <a href={event.rule.url} target="_blank" rel="noreferrer">
              Открыть источник
              <ExternalLink size={12} />
            </a>
          )}
        </div>
      </div>
      <div className="review-actions">
        <span>
          {event.review === "pending"
            ? "Ваше решение по событию"
            : event.review === "confirmed"
              ? "Отмечено вами как подтверждённое"
              : "Отмечено вами как отклонённое"}
        </span>
        <div>
          {event.review !== "pending" && (
            <button
              className="icon-button"
              title="Сбросить решение"
              aria-label="Сбросить решение"
              onClick={() => onReview("pending")}
            >
              <RotateCcw size={15} />
            </button>
          )}
          <button
            className={`button small secondary ${event.review === "dismissed" ? "chosen" : ""}`}
            onClick={() => onReview("dismissed")}
          >
            <X size={14} />
            Отклонить
          </button>
          <button
            className={`button small ${event.review === "confirmed" ? "confirmed" : "primary"}`}
            onClick={() => onReview("confirmed")}
          >
            <Check size={14} />
            Подтвердить
          </button>
        </div>
      </div>
    </section>
  );
}
