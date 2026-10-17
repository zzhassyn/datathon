import type {
  AnalysisJob,
  ApiConfig,
  AnalysisResult,
  ReviewStatus,
} from "../types";
import { demoJobs, DEMO_FRAME_URL, DEMO_VIDEO_URL } from "../api/demo";
import { validateLocalBaseUrl } from "../api";

const JOBS_KEY = "forte-video-analytics.jobs.v1";
const CONFIG_KEY = "forte-video-analytics.config.v1";
const DEFAULT_CONFIG: ApiConfig = {
  mode: import.meta.env.VITE_API_MODE === "http" ? "http" : "mock",
  baseUrl: "http://127.0.0.1:8000",
  apiKey: "",
};

function persistedOrigin(value: unknown): string {
  try {
    return typeof value === "string"
      ? validateLocalBaseUrl(value)
      : DEFAULT_CONFIG.baseUrl;
  } catch {
    return DEFAULT_CONFIG.baseUrl;
  }
}

function safeResult(
  result: AnalysisResult | undefined,
): AnalysisResult | undefined {
  if (
    !result ||
    !["violation", "no_violation"].includes(result.verdict) ||
    !Array.isArray(result.violations)
  )
    return undefined;
  return {
    verdict: result.verdict,
    violations: result.violations.map((v) => ({
      id: v.id,
      type: v.type,
      category: v.category,
      timecode_sec: v.timecode_sec,
      description: v.description,
      ...(typeof v.confidence === "number" ? { confidence: v.confidence } : {}),
      ...(v.rule
        ? {
            rule: {
              title: v.rule.title,
              ...(v.rule.note ? { note: v.rule.note } : {}),
              ...(/^https?:\/\//.test(v.rule.url ?? "")
                ? { url: v.rule.url }
                : {}),
            },
          }
        : {}),
      review: ["pending", "confirmed", "dismissed"].includes(v.review)
        ? v.review
        : "pending",
      evidence_frames: Array.isArray(v.evidence_frames)
        ? v.evidence_frames.map((frame) => ({
            timestamp_sec: frame.timestamp_sec,
            ...(frame.synthetic && frame.image_url === DEMO_FRAME_URL
              ? { image_url: DEMO_FRAME_URL, synthetic: true }
              : {}),
            ...(frame.boxes
              ? {
                  boxes: frame.boxes.map((b) => ({
                    x: b.x,
                    y: b.y,
                    width: b.width,
                    height: b.height,
                    label: b.label,
                    ...(b.start_sec !== undefined
                      ? { start_sec: b.start_sec }
                      : {}),
                    ...(b.end_sec !== undefined ? { end_sec: b.end_sec } : {}),
                  })),
                }
              : {}),
          }))
        : [],
    })),
  };
}

/** Only metadata is persisted. User videos and API evidence bytes live in this tab only. */
function metadataOnly(job: AnalysisJob): AnalysisJob {
  const synthetic = job.mode === "mock" && job.video.synthetic === true;
  return {
    id: job.id,
    mode: job.mode,
    status: job.status,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
    video: {
      id: job.video.id,
      name: job.video.name,
      size: job.video.size,
      duration: job.video.duration,
      available: synthetic,
      ...(synthetic
        ? { synthetic: true, url: DEMO_VIDEO_URL, previewUrl: DEMO_FRAME_URL }
        : {}),
    },
    ...(job.progress !== undefined ? { progress: job.progress } : {}),
    ...(job.demoScenario ? { demoScenario: job.demoScenario } : {}),
    ...(job.result ? { result: safeResult(job.result) } : {}),
    ...(job.error ? { error: job.error } : {}),
  };
}

function validJob(value: unknown): value is AnalysisJob {
  if (!value || typeof value !== "object") return false;
  const j = value as AnalysisJob;
  return (
    typeof j.id === "string" &&
    ["mock", "http"].includes(j.mode) &&
    ["uploading", "queued", "processing", "done", "failed"].includes(
      j.status,
    ) &&
    typeof j.createdAt === "string" &&
    Number.isFinite(Date.parse(j.createdAt)) &&
    typeof j.updatedAt === "string" &&
    Number.isFinite(Date.parse(j.updatedAt)) &&
    !!j.video &&
    typeof j.video.id === "string" &&
    typeof j.video.name === "string" &&
    typeof j.video.size === "number" &&
    Number.isFinite(j.video.size) &&
    j.video.size >= 0 &&
    typeof j.video.duration === "number" &&
    Number.isFinite(j.video.duration) &&
    j.video.duration >= 0
  );
}

export function loadJobs(): AnalysisJob[] {
  try {
    const raw = localStorage.getItem(JOBS_KEY);
    if (raw === null) return demoJobs();
    const jobs: unknown = JSON.parse(raw);
    if (!Array.isArray(jobs)) return demoJobs();
    return jobs.filter(validJob).map(metadataOnly);
  } catch {
    return demoJobs();
  }
}

export function saveJobs(jobs: AnalysisJob[]): void {
  try {
    localStorage.setItem(JOBS_KEY, JSON.stringify(jobs.map(metadataOnly)));
  } catch {
    /* Storage may be unavailable or full; the active session still works. */
  }
}

export function loadConfig(): ApiConfig {
  try {
    const value = JSON.parse(
      localStorage.getItem(CONFIG_KEY) ?? "null",
    ) as Partial<ApiConfig> | null;
    return {
      mode: value
        ? value.mode === "http"
          ? "http"
          : "mock"
        : DEFAULT_CONFIG.mode,
      baseUrl: persistedOrigin(
        value?.baseUrl ?? import.meta.env.VITE_API_BASE_URL,
      ),
      apiKey: "",
    };
  } catch {
    return { ...DEFAULT_CONFIG };
  }
}

export function saveConfig(config: ApiConfig): void {
  try {
    localStorage.setItem(
      CONFIG_KEY,
      JSON.stringify({
        mode: config.mode,
        baseUrl: persistedOrigin(config.baseUrl),
      }),
    );
  } catch {
    /* Keep the in-memory configuration. */
  }
}

export function saveReview(
  jobId: string,
  violationId: string,
  status: ReviewStatus,
): void {
  saveJobs(
    loadJobs().map((job) =>
      job.id === jobId && job.result
        ? {
            ...job,
            result: {
              ...job.result,
              violations: job.result.violations.map((v) =>
                v.id === violationId ? { ...v, review: status } : v,
              ),
            },
          }
        : job,
    ),
  );
}
