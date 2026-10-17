import type { AnalysisApi, AnalysisJob, ApiConfig, Video } from "../types";
import { createDemoResult } from "./demo";
import { parseJobResponse, parseSubmittedJob } from "./validation";

export { createDemoResult, demoJobs } from "./demo";

const UPLOAD_TIMEOUT_MS = 120_000;
const POLL_TIMEOUT_MS = 20_000;
const abortError = () => new DOMException("Действие отменено.", "AbortError");

export function validateLocalBaseUrl(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error(
      "Введите адрес локального API, например http://127.0.0.1:8000.",
    );
  }
  if (
    !["http:", "https:"].includes(url.protocol) ||
    !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    !["", "/"].includes(url.pathname)
  )
    throw new Error(
      "Разрешён только локальный API: localhost, 127.0.0.1 или [::1], без пути и параметров. Видео не отправляется на внешние серверы.",
    );
  return url.origin;
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(abortError());
    const finish = () => {
      signal?.removeEventListener("abort", abort);
      resolve();
    };
    const timer = setTimeout(finish, ms);
    const abort = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      reject(abortError());
    };
    signal?.addEventListener("abort", abort, { once: true });
  });
}

function makeJob(
  id: string,
  video: Video,
  mode: "mock" | "http",
  scenario?: "events" | "clean" | "error",
): AnalysisJob {
  const now = new Date().toISOString();
  return {
    id,
    video,
    mode,
    status: "queued",
    createdAt: now,
    updatedAt: now,
    progress: 0,
    ...(scenario ? { demoScenario: scenario } : {}),
  };
}

async function fetchJson(
  url: string,
  apiKey: string,
  signal?: AbortSignal,
): Promise<unknown> {
  if (signal?.aborted) throw abortError();
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, POLL_TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      headers: { "X-API-Key": apiKey },
      signal: controller.signal,
      cache: "no-store",
      redirect: "error",
    });
    if (!response.ok)
      throw new Error(
        `API вернул HTTP ${response.status}. Проверьте адрес сервера и API-ключ.`,
      );
    try {
      return await response.json();
    } catch {
      throw new Error("API вернул некорректный JSON.");
    }
  } catch (error) {
    if (signal?.aborted) throw abortError();
    if (timedOut)
      throw new Error(
        "Локальный API не ответил за 20 секунд. Попробуйте снова.",
      );
    if (error instanceof TypeError)
      throw new Error(
        "Локальный API недоступен. Проверьте сервер и настройки CORS.",
      );
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

async function upload(
  url: string,
  file: File,
  apiKey: string,
  onProgress?: (progress: number) => void,
  signal?: AbortSignal,
): Promise<unknown> {
  if (signal?.aborted) throw abortError();
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, UPLOAD_TIMEOUT_MS);
  const data = new FormData();
  data.append("file", file);
  onProgress?.(0);
  try {
    // fetch rejects redirects before following them. XHR cannot guarantee that the video stays local.
    const response = await fetch(url, {
      method: "POST",
      body: data,
      headers: { "X-API-Key": apiKey },
      signal: controller.signal,
      redirect: "error",
    });
    if (!response.ok)
      throw new Error(
        `API вернул HTTP ${response.status}. Проверьте адрес сервера и API-ключ.`,
      );
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      throw new Error("API вернул некорректный JSON.");
    }
    onProgress?.(100);
    return body;
  } catch (error) {
    if (signal?.aborted) throw abortError();
    if (timedOut)
      throw new Error("Загрузка в локальный API превысила 120 секунд.");
    if (error instanceof TypeError)
      throw new Error(
        "Не удалось отправить видео в локальный API. Проверьте сервер, CORS и отсутствие перенаправлений.",
      );
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

export function createApi(config: ApiConfig): AnalysisApi {
  if (config.mode === "mock")
    return {
      async submit(_file, video, scenario = "events", onProgress, signal) {
        for (const step of [0, 25, 60, 100]) {
          await wait(110, signal);
          onProgress?.(step);
        }
        const id = `demo-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
        return makeJob(id, video, "mock", scenario);
      },
      async poll(job, signal) {
        if (signal?.aborted) throw abortError();
        if (job.status === "done" || job.status === "failed") return job;
        const elapsed = Math.max(0, Date.now() - Date.parse(job.createdAt));
        const updatedAt = new Date().toISOString();
        if (elapsed >= 9_000) {
          if (job.demoScenario === "error")
            return {
              ...job,
              status: "failed",
              error:
                "Демонстрационный сценарий ошибки. Видео не анализировалось моделью.",
              progress: undefined,
              result: undefined,
              updatedAt,
            };
          return {
            ...job,
            status: "done",
            progress: 100,
            result: createDemoResult(job.demoScenario),
            error: undefined,
            updatedAt,
          };
        }
        return {
          ...job,
          status: elapsed < 1_500 ? "queued" : "processing",
          progress: Math.min(95, Math.round((elapsed / 9_000) * 100)),
          updatedAt,
        };
      },
    };
  const baseUrl = validateLocalBaseUrl(config.baseUrl);
  const apiKey = config.apiKey.trim();
  return {
    async submit(file, video, _scenario, onProgress, signal) {
      if (!file)
        throw new Error("Выберите видео для отправки в локальный API.");
      if (!apiKey)
        throw new Error(
          "Введите X-API-Key. Ключ хранится только в памяти текущей страницы.",
        );
      const response = await upload(
        `${baseUrl}/api/v1/analyze`,
        file,
        apiKey,
        onProgress,
        signal,
      );
      return makeJob(parseSubmittedJob(response), video, "http");
    },
    async poll(job, signal) {
      if (job.status === "done" || job.status === "failed") return job;
      if (!apiKey)
        throw new Error("Введите X-API-Key для проверки статуса задачи.");
      const response = await fetchJson(
        `${baseUrl}/api/v1/jobs/${encodeURIComponent(job.id)}`,
        apiKey,
        signal,
      );
      const parsed = parseJobResponse(
        response,
        job.video.duration || undefined,
      );
      return {
        ...job,
        ...parsed,
        progress: parsed.status === "done" ? 100 : undefined,
        updatedAt: new Date().toISOString(),
      };
    },
  };
}
