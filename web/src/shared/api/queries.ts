import { useQuery } from "@tanstack/react-query";
import { validJobId } from "../lib/urlState";
import { fetchCatalog, fetchJob, fetchReady } from "./client";
import { HttpError } from "./errors";
export const active = (status?: string) => status === "queued" || status === "running";
export const terminal = (status?: string) => status === "done" || status === "failed";
export const stopError = (error: unknown) =>
  error instanceof HttpError && [401, 404].includes(error.status);
export function pollInterval(status?: string, error?: unknown) {
  return stopError(error) || terminal(status) ? false : 1000;
}
export function useJob(id: string) {
  return useQuery({
    queryKey: ["job", id],
    queryFn: ({ signal }) => fetchJob(id, signal),
    enabled: validJobId(id),
    refetchInterval: (q) => pollInterval(q.state.data?.status, q.state.error),
    refetchIntervalInBackground: true,
    staleTime: (q) => (terminal(q.state.data?.status) ? Infinity : 0),
    retry: (n, error) => !stopError(error) && n < 2,
  });
}
export function useCatalog() {
  return useQuery({
    queryKey: ["catalog"],
    queryFn: ({ signal }) => fetchCatalog(signal),
    staleTime: Infinity,
    gcTime: Infinity,
    retry: (n, e) => !stopError(e) && n < 2,
  });
}
export function useReady() {
  return useQuery({
    queryKey: ["ready"],
    queryFn: ({ signal }) => fetchReady(signal),
    refetchInterval: 30000,
    refetchIntervalInBackground: false,
    retry: 1,
  });
}
