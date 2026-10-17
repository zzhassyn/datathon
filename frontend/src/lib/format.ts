import type { JobStatus } from "../types";
export const timecode = (seconds: number) => {
  const tenths = Math.round(
    (Number.isFinite(seconds) ? Math.max(0, seconds) : 0) * 10,
  );
  const whole = Math.floor(tenths / 10);
  return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}${tenths % 10 ? `.${tenths % 10}` : ""}`;
};
export const sizeLabel = (size: number) =>
  size === 0
    ? "Синтетическая запись"
    : size < 1024 * 1024
      ? `${Math.round(size / 1024)} КБ`
      : `${(size / 1024 / 1024).toFixed(1)} МБ`;
export const dateLabel = (iso: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Almaty",
  }).format(new Date(iso));
export const statusLabels: Record<JobStatus, string> = {
  uploading: "Загрузка",
  queued: "В очереди",
  processing: "Анализ",
  done: "Завершён",
  failed: "Ошибка",
};
export const isActive = (status: JobStatus) =>
  ["uploading", "queued", "processing"].includes(status);
