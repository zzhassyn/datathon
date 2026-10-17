import type { components } from "./schema";

// Generated transport types; only legal URLs are sanitized for presentation.
export type LegalView = Omit<components["schemas"]["UiLegalRef"], "url"> & { url: string | null };
export type EvidenceView = components["schemas"]["UiEvidence"];
export type EventView = Omit<components["schemas"]["UiViolation"], "legal"> & {
  legal: LegalView[];
};
export type JobView = Omit<
  Required<components["schemas"]["UiJobView"]>,
  "violations" | "signals"
> & { violations: EventView[]; signals: Required<components["schemas"]["UiSignal"]>[] };
function oneOf<const T extends string>(value: unknown, values: readonly T[]): T {
  const candidate = text(value);
  const found = values.find((entry) => entry === candidate);
  if (!found) throw new Error("Invalid enum value");
  return found;
}
export function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Invalid API object");
  return value as Record<string, unknown>;
}
export function text(value: unknown) {
  if (typeof value !== "string") throw new Error("Invalid API text");
  return value;
}
export function numeric(value: unknown) {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error("Invalid API number");
  return value;
}
const optionalText = (value: unknown) => (value == null ? null : text(value));
const optionalNumber = (value: unknown) => (value == null ? null : numeric(value));
function array(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw new Error("Invalid API list");
  return value;
}
export function mediaUrl(value: unknown) {
  const url = text(value);
  if (
    !/^\/api\/ui\/jobs\/[0-9a-f]{32}\/(?:video\.mp4|frames\/\d{1,2}_(?:event|zoom|context)\.jpg)$/.test(
      url,
    )
  )
    throw new Error("Invalid media URL");
  return url;
}
export function legalUrl(value: unknown) {
  try {
    const u = new URL(text(value));
    return u.protocol === "https:" &&
      u.hostname === "adilet.zan.kz" &&
      !u.username &&
      !u.password &&
      !u.port
      ? u.href
      : null;
  } catch {
    return null;
  }
}
export function decodeLegal(value: unknown): LegalView {
  const v = record(value);
  return {
    act_code: text(v.act_code),
    act_title_ru: text(v.act_title_ru),
    clause: optionalText(v.clause),
    quote_ru: optionalText(v.quote_ru),
    url: legalUrl(v.url),
    strength: oneOf(v.strength, ["direct", "indirect", "none"]),
    note_ru: optionalText(v.note_ru),
  };
}
export function decodeEvidence(value: unknown): EvidenceView {
  const v = record(value);
  return {
    url: mediaUrl(v.url),
    timestamp_sec: numeric(v.timestamp_sec),
    kind: oneOf(v.kind, ["event", "zoom", "context"]),
    width: numeric(v.width),
    height: numeric(v.height),
  };
}
function decodeEvent(value: unknown): EventView {
  const v = record(value);
  return {
    id: text(v.id),
    code: text(v.code),
    title_ru: text(v.title_ru),
    timecode_sec: numeric(v.timecode_sec),
    description_ru: text(v.description_ru),
    evidence: array(v.evidence).map(decodeEvidence),
    legal: array(v.legal ?? []).map(decodeLegal),
    legal_note_ru: optionalText(v.legal_note_ru),
    start_sec: numeric(v.start_sec),
    end_sec: numeric(v.end_sec),
    confidence: numeric(v.confidence),
    vlm_status: oneOf(v.vlm_status, ["confirmed", "downweighted", "unverified", "skipped"]),
    provisional: v.provisional === true,
  };
}
export function decodeJob(value: unknown, expectedId: string): JobView {
  const v = record(value);
  if (v.job_id !== expectedId) throw new Error("Job identity mismatch");
  const status = oneOf(v.status, ["queued", "running", "done", "failed"]);
  const verdict = v.verdict == null ? null : oneOf(v.verdict, ["violation", "no_violation"]);
  if (status === "done" && verdict !== "violation" && verdict !== "no_violation")
    throw new Error("Missing final verdict");
  if (status !== "done" && verdict !== null) throw new Error("Premature verdict");
  if (status === "done" && (verdict === "violation") !== array(v.violations).length > 0)
    throw new Error("Verdict mismatch");
  if (status === "failed" && !v.error) throw new Error("Missing failure reason");
  const video = v.video == null ? null : record(v.video);
  const error = v.error == null ? null : record(v.error);
  return {
    visibility: null,
    timings:
      v.timings == null
        ? null
        : Object.fromEntries(
            Object.entries(record(v.timings)).map(([key, value]) => [key, numeric(value)]),
          ),
    job_id: expectedId,
    pipeline_mode: oneOf(v.pipeline_mode, ["stub", "replay", "full"]),
    status,
    verdict,
    created_at: text(v.created_at),
    elapsed_sec: numeric(v.elapsed_sec),
    queue_position: optionalNumber(v.queue_position),
    eta_sec: optionalNumber(v.eta_sec),
    provisional: v.provisional === true,
    perception_level:
      v.perception_level == null ? null : oneOf(v.perception_level, ["full", "fast", "min"]),
    result_level:
      v.result_level == null ? null : oneOf(v.result_level, ["full", "partial", "rules"]),
    video: video
      ? {
          url: mediaUrl(video.url),
          duration_sec: numeric(video.duration_sec),
          width: numeric(video.width),
          height: numeric(video.height),
        }
      : null,
    violations: array(v.violations).map(decodeEvent),
    signals: array(v.signals ?? []).map((value) => {
      const s = record(value);
      return {
        id: text(s.id),
        code: text(s.code),
        title_ru: text(s.title_ru),
        t_sec: numeric(s.t_sec),
        description_ru: text(s.description_ru),
        evidence: array(s.evidence ?? []).map(decodeEvidence),
      };
    }),
    stages: array(v.stages ?? []).map((value) => {
      const s = record(value);
      return {
        code: oneOf(s.code, ["queued", "probe", "perception", "rules", "evidence", "vlm", "done"]),
        status: oneOf(s.status, ["pending", "running", "done", "skipped", "failed"]),
        progress: numeric(s.progress),
        elapsed_sec: optionalNumber(s.elapsed_sec),
        note_ru: optionalText(s.note_ru),
      };
    }),
    degradations: array(v.degradations ?? []).map((value) => {
      const d = record(value);
      return { stage: text(d.stage), reason_ru: text(d.reason_ru) };
    }),
    error: error ? { code: text(error.code), message_ru: text(error.message_ru) } : null,
  };
}
export function decodeCatalog(value: unknown) {
  const v = record(value);
  return {
    retention_hours: optionalNumber(v.retention_hours),
    types: array(v.types).map((value) => {
      const t = record(value);
      return {
        code: text(t.code),
        title_ru: text(t.title_ru),
        group: optionalText(t.group),
        verdict_impact: t.verdict_impact === true,
        legal: array(t.legal ?? []).map(decodeLegal),
      };
    }),
  };
}
