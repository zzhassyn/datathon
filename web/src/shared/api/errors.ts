import { ru } from "../i18n/ru";
export class HttpError extends Error {
  constructor(
    public status: number,
    public retryAt = 0,
  ) {
    super(errorMessage(status));
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
