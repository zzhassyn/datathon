import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { readRecent, removeRecent, saveRecent } from "./recentStore";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());
it("limits history and deduplicates a refreshed job", () => {
  for (let i = 0; i < 12; i++)
    saveRecent({
      id: i.toString(16).padStart(32, "0"),
      fileName: `${i}.mov`,
      createdAt: new Date().toISOString(),
      lastStatus: "queued",
      verdict: null,
      nViolations: 0,
    });
  expect(readRecent()).toHaveLength(10);
  const row = readRecent()[0];
  saveRecent({ ...row, lastStatus: "done" });
  expect(readRecent()).toHaveLength(10);
  expect(readRecent()[0].lastStatus).toBe("done");
  removeRecent(row.id);
  expect(readRecent()).toHaveLength(9);
});
it("survives denied storage", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("denied");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("denied");
  });
  expect(readRecent()).toEqual([]);
  expect(() =>
    saveRecent({
      id: "a".repeat(32),
      fileName: "test.mov",
      createdAt: "",
      lastStatus: "queued",
      verdict: null,
      nViolations: 0,
    }),
  ).not.toThrow();
});
