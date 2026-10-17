import { useEffect, useRef, useState } from "react";
import {
  X,
  UploadCloud,
  Film,
  ShieldCheck,
  Play,
  AlertCircle,
  ArrowRight,
  LoaderCircle,
} from "lucide-react";
import type { ApiMode, Video } from "../types";
import { inspectVideo } from "../lib/media";
import { sizeLabel, timecode } from "../lib/format";
export function UploadModal({
  mode,
  onClose,
  onSubmit,
}: {
  mode: ApiMode;
  onClose: () => void;
  onSubmit: (
    file: File | null,
    video: Video,
    scenario: "events" | "clean" | "error",
  ) => Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [video, setVideo] = useState<Video | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [scenario, setScenario] = useState<"events" | "clean" | "error">(
    "events",
  );
  const input = useRef<HTMLInputElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const pendingUrl = useRef<string | undefined>(undefined);
  const submitted = useRef(false);
  const generation = useRef(0);
  useEffect(() => {
    closeRef.current?.focus();
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
      if (event.key === "Tab") {
        const nodes = Array.from(
          document.querySelectorAll<HTMLElement>(
            '[role="dialog"] button:not(:disabled), [role="dialog"] input, [role="dialog"] select, [role="dialog"] video',
          ),
        ).filter((el) => el.offsetParent !== null);
        const first = nodes[0],
          last = nodes.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [busy, onClose]);
  useEffect(
    () => () => {
      generation.current++;
      if (pendingUrl.current && !submitted.current)
        URL.revokeObjectURL(pendingUrl.current);
    },
    [],
  );
  async function choose(selected?: File) {
    if (!selected) return;
    setError("");
    if (!/\.(mov|mp4|webm|m4v)$/i.test(selected.name)) {
      setError("Выберите видео в формате MOV, MP4, WebM или M4V.");
      return;
    }
    if (!selected.size) {
      setError("Файл пуст. Выберите другую запись.");
      return;
    }
    setReading(true);
    const current = ++generation.current;
    const info = await inspectVideo(selected);
    if (current !== generation.current) {
      if (info.url) URL.revokeObjectURL(info.url);
      return;
    }
    if (pendingUrl.current) URL.revokeObjectURL(pendingUrl.current);
    pendingUrl.current = info.url;
    setFile(selected);
    setVideo(info);
    setReading(false);
  }
  function selectDemo() {
    generation.current++;
    setReading(false);
    if (pendingUrl.current) URL.revokeObjectURL(pendingUrl.current);
    pendingUrl.current = undefined;
    setFile(null);
    setError("");
    setVideo({
      id: crypto.randomUUID(),
      name: "Кассовая зона · демозапись.webm",
      size: 0,
      duration: 24,
      url: "/demo/cash-desk.webm",
      previewUrl: "/demo/cash-desk.svg",
      available: true,
      synthetic: true,
    });
  }
  async function submit() {
    if (!video || busy) return;
    setBusy(true);
    setError("");
    try {
      await onSubmit(file, video, scenario);
      submitted.current = true;
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось отправить видео");
      setBusy(false);
    }
  }
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <section
        className="upload-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="upload-title"
      >
        <div className="modal-header">
          <span className="modal-icon">
            <UploadCloud size={24} />
          </span>
          <button
            ref={closeRef}
            className="icon-button"
            onClick={onClose}
            disabled={busy}
            aria-label="Закрыть загрузку"
          >
            <X size={20} />
          </button>
        </div>
        <div className="eyebrow">НОВАЯ ВИДЕОЗАПИСЬ</div>
        <h2 id="upload-title">Начнём с загрузки</h2>
        <p className="muted">Добавьте запись кассовой операции для проверки.</p>
        <input
          ref={input}
          className="sr-only"
          type="file"
          accept=".mov,.mp4,.webm,.m4v,video/quicktime,video/mp4,video/webm"
          onChange={(e) => {
            const chosen = e.target.files?.[0];
            e.currentTarget.value = "";
            void choose(chosen);
          }}
        />
        {!video ? (
          <>
            <button
              className={`dropzone ${dragging ? "dragging" : ""}`}
              onClick={() => input.current?.click()}
              disabled={reading || busy}
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                void choose(e.dataTransfer.files[0]);
              }}
            >
              <span className="drop-icon">
                {reading ? (
                  <LoaderCircle className="spin" size={28} />
                ) : (
                  <UploadCloud size={28} />
                )}
              </span>
              <strong>
                {reading ? "Читаем свойства видео…" : "Перетащите видео сюда"}
              </strong>
              <span>
                или <u>выберите файл</u> на устройстве
              </span>
              <small>MOV · MP4 · WEBM · M4V</small>
            </button>
            {mode === "mock" && (
              <button className="demo-choice" onClick={selectDemo}>
                <span>
                  <Play size={17} />
                  <span>
                    Попробовать на демозаписи
                    <small>Синтетическая сцена · 24 секунды</small>
                  </span>
                </span>
                <ArrowRight size={17} />
              </button>
            )}
          </>
        ) : (
          <div className="upload-preview">
            <video
              key={video.url}
              src={video.url}
              poster={video.previewUrl}
              controls
              muted
              playsInline
              onError={() =>
                setError(
                  "Предпросмотр недоступен для этого кодека. Файл по-прежнему можно отправить на анализ.",
                )
              }
            />
            <div className="selected-file">
              <Film size={20} />
              <div>
                <strong>{video.name}</strong>
                <small>
                  {sizeLabel(video.size)} ·{" "}
                  {video.duration
                    ? timecode(video.duration)
                    : "Длительность недоступна"}
                </small>
              </div>
              <button
                className="text-button"
                disabled={busy}
                onClick={() => {
                  setVideo(null);
                  setFile(null);
                  if (pendingUrl.current)
                    URL.revokeObjectURL(pendingUrl.current);
                  pendingUrl.current = undefined;
                }}
              >
                Изменить
              </button>
            </div>
          </div>
        )}
        {mode === "mock" && (
          <label className="field-label">
            Демонстрационный сценарий
            <select
              value={scenario}
              disabled={busy}
              onChange={(e) => setScenario(e.target.value as typeof scenario)}
            >
              <option value="events">Найдены предполагаемые события</option>
              <option value="clean">События не обнаружены</option>
              <option value="error">Ошибка обработки</option>
            </select>
          </label>
        )}
        <div className="inline-notice">
          <ShieldCheck size={18} />
          <span>
            {mode === "mock"
              ? "Деморежим: файл остаётся в браузере. Результаты иллюстрируют интерфейс и не описывают содержимое вашей записи."
              : "Видео будет отправлено только в настроенный локальный API. Ключ хранится в памяти страницы."}
          </span>
        </div>
        {error && (
          <div className="error-notice" role="alert">
            <AlertCircle size={16} />
            {error}
          </div>
        )}
        <div className="modal-footer">
          <button
            className="button secondary"
            onClick={onClose}
            disabled={busy}
          >
            Отмена
          </button>
          <button
            className="button primary"
            disabled={!video || busy || reading}
            onClick={() => void submit()}
          >
            {busy ? <LoaderCircle size={17} className="spin" /> : <ScanIcon />}
            {busy
              ? "Отправляем…"
              : mode === "mock"
                ? "Запустить демоанализ"
                : "Отправить на анализ"}
          </button>
        </div>
      </section>
    </div>
  );
}
function ScanIcon() {
  return <ArrowRight size={17} />;
}
