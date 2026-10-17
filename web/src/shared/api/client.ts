import createClient from "openapi-fetch";
import { ru } from "../i18n/ru";
import { decodeCatalog, decodeJob, record } from "./decode";
import { HttpError, retryDeadline } from "./errors";
import type { paths } from "./schema";
export const api = createClient<paths>({
  baseUrl: "",
  fetch: (request) =>
    fetch(request, { signal: AbortSignal.any([request.signal, AbortSignal.timeout(15_000)]) }),
});
function check(response: Response, body?: unknown) {
  if (!response.ok)
    throw new HttpError(response.status, retryDeadline(response.headers.get("Retry-After")), body);
}
export async function fetchJob(id: string, signal?: AbortSignal) {
  const { data, error, response } = await api.GET("/api/ui/jobs/{job_id}", {
    params: { path: { job_id: id } },
    signal,
  });
  check(response, error);
  try {
    return decodeJob(data, id);
  } catch {
    throw new Error(ru.invalidResponse);
  }
}
export async function fetchCatalog(signal?: AbortSignal) {
  const { data, error, response } = await api.GET("/api/ui/catalog", { signal });
  check(response, error);
  try {
    return decodeCatalog(data);
  } catch {
    throw new Error(ru.invalidResponse);
  }
}
export async function fetchReady(signal?: AbortSignal) {
  const { data, error, response } = await api.GET("/ready", { signal });
  if (response.status !== 503) check(response);
  const value = record(data ?? error);
  return { ready: value.ready === true, mode: value.pipeline_mode };
}
export async function openSession(code: string) {
  // Current S1 schema omits requestBody: transport-only escape until S0 exports it.
  const response = await fetch("/api/ui/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  check(response, await response.json().catch(() => null));
}
