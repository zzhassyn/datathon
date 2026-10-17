import { ru } from "../i18n/ru";
export class HttpError extends Error {
  constructor(
    public status: number,
    public retryAt = 0,
    body?: unknown,
  ) {
    super(errorMessage(status));
    if (body && typeof body === "object" && "code" in body && "message_ru" in body) {
      const codes = [
        "FILE_TOO_LARGE",
        "UNSUPPORTED_MEDIA",
        "INVALID_REQUEST",
        "UPLOAD_TIMEOUT",
        "UPLOAD_ABORTED",
        "RATE_LIMITED",
        "INSUFFICIENT_STORAGE",
        "NOT_FOUND",
        "UNAUTHORIZED",
        "INTERNAL",
      ];
      if (
        typeof body.code === "string" &&
        codes.includes(body.code) &&
        typeof body.message_ru === "string" &&
        body.message_ru.trim()
      ) {
        this.message = body.message_ru;
        if (
          !retryAt &&
          "retry_after_sec" in body &&
          typeof body.retry_after_sec === "number" &&
          Number.isFinite(body.retry_after_sec) &&
          body.retry_after_sec > 0
        )
          this.retryAt = Date.now() + body.retry_after_sec * 1000;
      }
    }
  }
}
export const errorMessage = (status: number) =>
  ({
    401: ru.access,
    404: ru.notFound,
    413: ru.tooLarge,
    415: ru.unsupported,
    422: ru.noFile,
    429: ru.rateLimited,
    507: ru.noDisk,
    0: ru.network,
    499: ru.cancelled,
    502: ru.server,
  })[status] || ru.server;
export function retryDeadline(value: string | null, now = Date.now()) {
  if (!value) return 0;
  const seconds = Number(value);
  return Number.isFinite(seconds)
    ? now + Math.max(0, seconds) * 1000
    : Math.max(now, Date.parse(value) || now);
}
