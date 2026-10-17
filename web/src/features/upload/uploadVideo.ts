import { record, text } from "../../shared/api/decode";
import { HttpError, retryDeadline } from "../../shared/api/errors";
import { ru } from "../../shared/i18n/ru";
import { validJobId } from "../../shared/lib/urlState";
export type UploadProgress = {
  loaded: number;
  total: number;
  percent: number;
  bytesPerSecond: number;
  etaSec: number | null;
};
export function uploadVideo(
  file: File,
  onProgress: (p: UploadProgress) => void,
  signal: AbortSignal,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let settled = false;
    let previousTime = performance.now();
    let previousBytes = 0;
    let speed = 0;
    const finish = (error?: Error, id?: string) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", abort);
      if (error) reject(error);
      else resolve(id as string);
    };
    const abort = () => {
      xhr.abort();
      finish(new HttpError(499));
    };
    if (signal.aborted) {
      finish(new HttpError(499));
      return;
    }
    xhr.open("POST", "/api/ui/jobs");
    xhr.timeout = 180000;
    xhr.upload.onprogress = (event) => {
      const now = performance.now();
      const dt = (now - previousTime) / 1000;
      if (dt > 0) {
        const instant = (event.loaded - previousBytes) / dt;
        speed = speed ? speed * 0.7 + instant * 0.3 : instant;
        previousTime = now;
        previousBytes = event.loaded;
      }
      const total = event.lengthComputable ? event.total : file.size;
      const loaded = Math.min(event.loaded, total);
      onProgress({
        loaded,
        total,
        percent: Math.min(100, Math.round((loaded / total) * 100)),
        bytesPerSecond: speed,
        etaSec: speed > 0 ? (total - loaded) / speed : null,
      });
    };
    xhr.onerror = () => finish(new HttpError(0));
    xhr.ontimeout = () => finish(new HttpError(0));
    xhr.onabort = () => finish(new HttpError(499));
    xhr.onload = () => {
      if (xhr.status !== 200) {
        let body: unknown;
        try {
          body = JSON.parse(xhr.responseText);
        } catch {
          /* Non-JSON failures use safe local text. */
        }
        finish(
          new HttpError(xhr.status, retryDeadline(xhr.getResponseHeader("Retry-After")), body),
        );
        return;
      }
      try {
        const id = text(record(JSON.parse(xhr.responseText)).job_id);
        if (!validJobId(id)) throw new Error();
        finish(undefined, id);
      } catch {
        finish(new Error(ru.invalidResponse));
      }
    };
    signal.addEventListener("abort", abort, { once: true });
    const body = new FormData();
    body.append("file", file);
    xhr.send(body);
  });
}
