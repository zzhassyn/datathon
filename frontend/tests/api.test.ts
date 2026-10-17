import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createApi,
  createDemoResult,
  demoJobs,
  validateLocalBaseUrl,
} from "../src/api";
import { parseJobResponse, parseResult } from "../src/api/validation";
import type { AnalysisJob, ApiConfig, Video } from "../src/types";

const config: ApiConfig = {
  mode: "http",
  baseUrl: "http://127.0.0.1:8000",
  apiKey: "session-only-key",
};
const video: Video = {
  id: "video-1",
  name: "example.mp4",
  duration: 24,
  size: 3,
  available: true,
  url: "blob:video",
};
const job: AnalysisJob = {
  id: "job/1",
  video,
  mode: "http",
  status: "queued",
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
};
// A base64 JPEG prefix is sufficient for transport validation; pixel decoding belongs to the browser.
const eventResponse = () => ({
  status: "done",
  verdict: "violation",
  violations: [
    {
      type: "Needs review",
      description: "Inspect this candidate.",
      timecode_sec: 7.2,
      evidence_frames: [{ timestamp_sec: 7.2, image_base64: "/9j/AAAA" }],
    },
  ],
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("local HTTP transport", () => {
  it.each([
    "https://external.example",
    "http://localhost.attacker.example:8000",
    "file:///tmp/video",
    "http://user:pass@localhost:8000",
    "http://127.0.0.1:8000/path",
    "http://127.0.0.1:8000?key=secret",
  ])("rejects unsafe API address %s", (address) => {
    expect(() => createApi({ ...config, baseUrl: address })).toThrow(
      /локальный API/,
    );
  });
  it.each([
    "http://localhost:8000/",
    "http://127.0.0.1:8000",
    "http://[::1]:8000",
  ])("accepts loopback %s", (address) => {
    expect(validateLocalBaseUrl(address)).toBe(new URL(address).origin);
  });
  it("submits multipart video to the documented endpoint and keeps the session key in headers", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ job_id: "backend-7" }), { status: 202 }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["abc"], "example.mp4", { type: "video/mp4" });
    const progress = vi.fn();
    const result = await createApi(config).submit(
      file,
      video,
      undefined,
      progress,
    );
    expect(result).toMatchObject({
      id: "backend-7",
      mode: "http",
      status: "queued",
    });
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/api/v1/analyze");
    expect(options.method).toBe("POST");
    expect(options.headers).toEqual({ "X-API-Key": "session-only-key" });
    expect(options.body).toBeInstanceOf(FormData);
    expect((options.body as FormData).get("file")).toBe(file);
    expect(options.redirect).toBe("error");
    expect(progress.mock.calls.map(([value]) => value)).toEqual([0, 100]);
  });
  it("polls an encoded task id and validates the response without inventing confidence", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify(eventResponse())));
    vi.stubGlobal("fetch", fetchMock);
    const result = await createApi(config).poll(job);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://127.0.0.1:8000/api/v1/jobs/job%2F1",
    );
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      redirect: "error",
      cache: "no-store",
    });
    expect(result.status).toBe("done");
    expect(result.result?.violations[0].confidence).toBeUndefined();
    expect(result.result?.violations[0].review).toBe("pending");
  });
  it("rejects backend and malformed responses instead of substituting demo results", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response("down", { status: 503 }))
      .mockResolvedValueOnce(new Response("not JSON"))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ status: "done", verdict: "no_violation" }),
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    await expect(createApi(config).poll(job)).rejects.toThrow("HTTP 503");
    await expect(createApi(config).poll(job)).rejects.toThrow(
      "некорректный JSON",
    );
    await expect(createApi(config).poll(job)).rejects.toThrow("violations");
  });
  it("keeps failed processing separate from a no-violation result", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ status: "failed", error: "Model unavailable" }),
          ),
        ),
    );
    const result = await createApi(config).poll(job);
    expect(result.status).toBe("failed");
    expect(result.error).toBe("Model unavailable");
    expect(result.result).toBeUndefined();
  });
  it("requires a file and a key before making any request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(createApi(config).submit(null, video)).rejects.toThrow(
      "Выберите видео",
    );
    await expect(
      createApi({ ...config, apiKey: "" }).poll(job),
    ).rejects.toThrow("X-API-Key");
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("supports pre-aborted requests without network activity", async () => {
    const controller = new AbortController();
    controller.abort();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(
      createApi(config).poll(job, controller.signal),
    ).rejects.toMatchObject({ name: "AbortError" });
    await expect(
      createApi(config).submit(
        new File(["abc"], "file.mp4"),
        video,
        undefined,
        undefined,
        controller.signal,
      ),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("ends polling after its finite timeout", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url, options) =>
          new Promise((_resolve, reject) =>
            options.signal.addEventListener("abort", () =>
              reject(new DOMException("aborted", "AbortError")),
            ),
          ),
      ),
    );
    const expectation = expect(createApi(config).poll(job)).rejects.toThrow(
      "20 секунд",
    );
    await vi.advanceTimersByTimeAsync(20_000);
    await expectation;
  });
});

describe("API contract validation", () => {
  it("accepts a successful clean result only with an empty violations array", () => {
    expect(
      parseJobResponse({
        status: "done",
        verdict: "no_violation",
        violations: [],
      }),
    ).toEqual({
      status: "done",
      result: { verdict: "no_violation", violations: [] },
    });
    expect(() =>
      parseResult({
        verdict: "no_violation",
        violations: eventResponse().violations,
      }),
    ).toThrow("противоречит");
    expect(() => parseResult({ verdict: "violation", violations: [] })).toThrow(
      "противоречит",
    );
  });
  it.each([-1, Infinity, NaN, 25])(
    "rejects invalid event timestamp %s",
    (timecode_sec) => {
      const response = eventResponse();
      response.violations[0].timecode_sec = timecode_sec;
      expect(() => parseResult(response, 24)).toThrow("timecode_sec");
    },
  );
  it("accepts zero as the beginning of a video", () => {
    const response = eventResponse();
    response.violations[0].timecode_sec = 0;
    expect(parseResult(response, 24).violations[0].timecode_sec).toBe(0);
  });
  it("requires one to three JPEG evidence frames", () => {
    const response = eventResponse();
    response.violations[0].evidence_frames = [];
    expect(() => parseResult(response)).toThrow("от 1 до 3");
    response.violations[0].evidence_frames = Array.from({ length: 4 }, () => ({
      timestamp_sec: 1,
      image_base64: "/9j/AAAA",
    }));
    expect(() => parseResult(response)).toThrow("от 1 до 3");
    response.violations[0].evidence_frames = [
      { timestamp_sec: 1, image_base64: "<svg>script</svg>" },
    ];
    expect(() => parseResult(response)).toThrow("JPEG");
  });
  it("rejects unknown states and missing event fields", () => {
    expect(() => parseJobResponse({ status: "surprise" })).toThrow("status");
    const response = eventResponse();
    response.violations[0].description = "";
    expect(() => parseResult(response)).toThrow("description");
  });
});

describe("explicit synthetic demo", () => {
  it("provides five demo records and does not invent model confidence", () => {
    expect(demoJobs()).toHaveLength(5);
    const result = createDemoResult();
    expect(result.violations).toHaveLength(3);
    expect(
      result.violations.every(
        (v) =>
          v.confidence === undefined &&
          v.timecode_sec <= 24 &&
          v.evidence_frames.every((f) => f.synthetic),
      ),
    ).toBe(true);
    expect(createDemoResult("clean")).toEqual({
      verdict: "no_violation",
      violations: [],
    });
    expect(() => createDemoResult("error")).toThrow("ошибка");
  });
  it("progresses queued → processing → done without sending network requests", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi({ ...config, mode: "mock" });
    const pending = api.submit(null, video, "events");
    await vi.advanceTimersByTimeAsync(440);
    let result = await pending;
    expect(result.status).toBe("queued");
    await vi.advanceTimersByTimeAsync(2_000);
    result = await api.poll(result);
    expect(result.status).toBe("processing");
    await vi.advanceTimersByTimeAsync(7_000);
    result = await api.poll(result);
    expect(result.status).toBe("done");
    expect(result.result?.verdict).toBe("violation");
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("simulates failure without manufacturing a clean result", async () => {
    const result = await createApi({ ...config, mode: "mock" }).poll({
      ...job,
      mode: "mock",
      demoScenario: "error",
      createdAt: new Date(Date.now() - 10_000).toISOString(),
    });
    expect(result.status).toBe("failed");
    expect(result.result).toBeUndefined();
  });
});
