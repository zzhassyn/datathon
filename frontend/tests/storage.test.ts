import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createDemoResult, demoJobs } from "../src/api";
import {
  loadConfig,
  loadJobs,
  saveConfig,
  saveJobs,
  saveReview,
} from "../src/lib/storage";
import type { AnalysisJob } from "../src/types";

let store: Map<string, string>;
beforeEach(() => {
  store = new Map();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => store.set(key, value),
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("privacy-preserving local history", () => {
  it("seeds explicit synthetic records on the first visit", () => {
    const jobs = loadJobs();
    expect(jobs).toHaveLength(5);
    expect(
      jobs.every((job) => job.mode === "mock" && job.video.synthetic),
    ).toBe(true);
  });
  it("never stores user video blobs, previews, base64, or arbitrary properties", () => {
    const result = createDemoResult();
    result.violations[0].evidence_frames[0] = {
      timestamp_sec: 7.2,
      image_base64: "sensitive-frame-bytes",
      image_url: "blob:private-evidence",
    };
    const real: AnalysisJob & { apiKey: string } = {
      ...demoJobs()[0],
      id: "real",
      mode: "http",
      apiKey: "do-not-store-this-key",
      result,
      video: {
        id: "upload",
        name: "local.mp4",
        size: 500,
        duration: 24,
        url: "blob:private-video",
        previewUrl: "data:image/jpeg;base64,private-preview",
        available: true,
      },
    };
    saveJobs([real]);
    const serialized = [...store.values()].join("");
    expect(serialized).not.toContain("blob:");
    expect(serialized).not.toContain("base64");
    expect(serialized).not.toContain("sensitive-frame-bytes");
    expect(serialized).not.toContain("do-not-store-this-key");
    const restored = loadJobs()[0];
    expect(restored.video.available).toBe(false);
    expect(restored.video.url).toBeUndefined();
    expect(restored.video.previewUrl).toBeUndefined();
    expect(restored.result?.violations[0].evidence_frames[0]).toEqual({
      timestamp_sec: 7.2,
    });
  });
  it("preserves only known synthetic media URLs", () => {
    saveJobs(demoJobs());
    expect(loadJobs()[0].video).toMatchObject({
      available: true,
      url: "/demo/cash-desk.webm",
    });
    expect(
      loadJobs()[0].result?.violations[0].evidence_frames[0].image_url,
    ).toBe("/demo/cash-desk.svg");
  });
  it("does not re-seed history after the user has cleared it", () => {
    saveJobs([]);
    expect(loadJobs()).toEqual([]);
  });
  it("restores review status without persisting any video contents", () => {
    const jobs = demoJobs();
    saveJobs(jobs);
    saveReview(jobs[0].id, jobs[0].result!.violations[0].id, "dismissed");
    expect(loadJobs()[0].result?.violations[0].review).toBe("dismissed");
  });
  it("falls back safely when storage is corrupted or unavailable", () => {
    store.set("forte-video-analytics.jobs.v1", "{broken");
    expect(loadJobs()).toHaveLength(5);
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("denied");
      },
    });
    expect(() => saveJobs(demoJobs())).not.toThrow();
    expect(loadJobs()).toHaveLength(5);
  });
});

describe("session-only API credentials", () => {
  it("persists mode and origin but excludes API key", () => {
    saveConfig({
      mode: "http",
      baseUrl: "http://localhost:9000",
      apiKey: "very-private-key",
    });
    expect([...store.values()].join("")).not.toContain("very-private-key");
    expect(loadConfig()).toEqual({
      mode: "http",
      baseUrl: "http://localhost:9000",
      apiKey: "",
    });
  });
  it("discards keys left by any previous saved format", () => {
    store.set(
      "forte-video-analytics.config.v1",
      JSON.stringify({
        mode: "http",
        baseUrl: "http://localhost:8000",
        apiKey: "old-key",
      }),
    );
    expect(loadConfig().apiKey).toBe("");
  });
  it("does not persist credentials or secrets embedded in a malformed API address", () => {
    saveConfig({
      mode: "http",
      baseUrl: "http://user:password@localhost:8000?apiKey=private-query",
      apiKey: "private-header",
    });
    expect([...store.values()].join("")).not.toMatch(
      /password|private-query|private-header/,
    );
    expect(loadConfig().baseUrl).toBe("http://127.0.0.1:8000");
  });
});
