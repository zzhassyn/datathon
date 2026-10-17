export type ApiMode = "mock" | "http";
export type Timestamp = number;
export type JobStatus =
  | "uploading"
  | "queued"
  | "processing"
  | "done"
  | "failed";
export type ReviewStatus = "pending" | "confirmed" | "dismissed";
export interface Video {
  id: string;
  name: string;
  size: number;
  duration: number;
  url?: string;
  previewUrl?: string;
  available: boolean;
  synthetic?: boolean;
}
export interface BoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
  label: string;
  start_sec?: number;
  end_sec?: number;
}
export interface Evidence {
  timestamp_sec: Timestamp;
  image_base64?: string;
  image_url?: string;
  boxes?: BoundingBox[];
  synthetic?: boolean;
}
export interface Violation {
  id: string;
  type: string;
  category: string;
  timecode_sec: Timestamp;
  description: string;
  confidence?: number;
  evidence_frames: Evidence[];
  rule?: { title: string; url?: string; note?: string };
  review: ReviewStatus;
}
export interface AnalysisResult {
  verdict: "violation" | "no_violation";
  violations: Violation[];
}
export interface AnalysisJob {
  id: string;
  video: Video;
  status: JobStatus;
  createdAt: string;
  updatedAt: string;
  mode: ApiMode;
  progress?: number;
  result?: AnalysisResult;
  error?: string;
  demoScenario?: "events" | "clean" | "error";
}
export interface ApiConfig {
  mode: ApiMode;
  baseUrl: string;
  apiKey: string;
}
export interface AnalysisApi {
  submit(
    file: File | null,
    video: Video,
    scenario?: "events" | "clean" | "error",
    onProgress?: (progress: number) => void,
    signal?: AbortSignal,
  ): Promise<AnalysisJob>;
  poll(job: AnalysisJob, signal?: AbortSignal): Promise<AnalysisJob>;
}
