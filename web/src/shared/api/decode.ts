// Temporary boundary for S1 a46acdb's untyped OpenAPI responses.
// These are inferred presentation values, NOT an agreed UiJobView contract.
// Replace the boundary with generated S0 components when contracts/ui.py is exported.
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
export function decodeLegal(value: unknown) {
  const v = record(value);
  return {
    act_title_ru: text(v.act_title_ru),
    clause: optionalText(v.clause),
    quote_ru: optionalText(v.quote_ru),
    url: legalUrl(v.url),
    strength: optionalText(v.strength),
  };
}
export function decodeEvidence(value: unknown) {
  const v = record(value);
  return {
    url: mediaUrl(v.url),
    timestamp_sec: numeric(v.timestamp_sec),
    kind: optionalText(v.kind),
    width: optionalNumber(v.width),
    height: optionalNumber(v.height),
  };
}
function decodeEvent(value: unknown) {
  const v = record(value);
  return {
    id: optionalText(v.id),
    code: optionalText(v.code),
    title_ru: text(v.title_ru),
    timecode_sec: numeric(v.timecode_sec),
    description_ru: text(v.description_ru),
    evidence: array(v.evidence).map(decodeEvidence),
    legal: array(v.legal ?? []).map(decodeLegal),
    start_sec: optionalNumber(v.start_sec),
    end_sec: optionalNumber(v.end_sec),
    confidence: optionalNumber(v.confidence),
    vlm_status: optionalText(v.vlm_status),
    provisional: v.provisional === true,
  };
}
export function decodeJob(value: unknown, expectedId: string) {
  const v = record(value);
  if (v.job_id !== expectedId) throw new Error("Job identity mismatch");
  const status = text(v.status);
  const verdict = optionalText(v.verdict);
  if (status === "done" && verdict !== "violation" && verdict !== "no_violation")
    throw new Error("Missing final verdict");
  const video = v.video == null ? null : record(v.video);
  const error = v.error == null ? null : record(v.error);
  return {
    job_id: expectedId,
    status,
    verdict,
    created_at: text(v.created_at),
    elapsed_sec: numeric(v.elapsed_sec),
    queue_position: optionalNumber(v.queue_position),
    eta_sec: optionalNumber(v.eta_sec),
    provisional: v.provisional === true,
    perception_level: optionalText(v.perception_level),
    result_level: optionalText(v.result_level),
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
        id: optionalText(s.id),
        title_ru: text(s.title_ru),
        t_sec: numeric(s.t_sec),
        description_ru: text(s.description_ru),
        evidence: array(s.evidence ?? []).map(decodeEvidence),
      };
    }),
    stages: array(v.stages ?? []).map((value) => {
      const s = record(value);
      return {
        code: text(s.code),
        status: text(s.status),
        progress: optionalNumber(s.progress),
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
export type JobView = ReturnType<typeof decodeJob>;
export type EventView = JobView["violations"][number];
export type EvidenceView = ReturnType<typeof decodeEvidence>;
export type LegalView = ReturnType<typeof decodeLegal>;
