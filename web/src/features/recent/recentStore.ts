import { validJobId } from "../../shared/lib/urlState";

const KEY = "cashguard.recent.v1";
export type Recent = {
  id: string;
  fileName: string;
  createdAt: string;
  lastStatus: string;
  verdict: string | null;
  nViolations: number;
};
export function readRecent(): Recent[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(value)
      ? value
          .filter(
            (v): v is Recent =>
              v &&
              typeof v.id === "string" &&
              validJobId(v.id) &&
              typeof v.fileName === "string" &&
              typeof v.createdAt === "string",
          )
          .slice(0, 10)
      : [];
  } catch {
    return [];
  }
}
export function saveRecent(value: Recent) {
  try {
    localStorage.setItem(
      KEY,
      JSON.stringify([value, ...readRecent().filter((v) => v.id !== value.id)].slice(0, 10)),
    );
  } catch {
    /* Storage may be disabled. */
  }
}
export function removeRecent(id?: string) {
  try {
    if (id) localStorage.setItem(KEY, JSON.stringify(readRecent().filter((v) => v.id !== id)));
    else localStorage.removeItem(KEY);
  } catch {
    /* Storage may be disabled. */
  }
}
