import type {
  AnalysisResult,
  BoundingBox,
  Evidence,
  Violation,
} from "../types";

type JsonObject = Record<string, unknown>;
function object(value: unknown, label: string): JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error(`Некорректный ответ API: ${label}.`);
  return value as JsonObject;
}
function requiredString(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim())
    throw new Error(`Некорректный ответ API: ${label} не заполнено.`);
  return value.trim();
}
function timestamp(
  value: unknown,
  duration: number | undefined,
  label: string,
): number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < 0 ||
    (duration && value > duration)
  )
    throw new Error(`Некорректный ответ API: ${label} вне диапазона видео.`);
  return value;
}
function parseBoxes(value: unknown): BoundingBox[] | undefined {
  if (value === undefined) return undefined;
  if (!Array.isArray(value))
    throw new Error("Некорректный ответ API: boxes должен быть массивом.");
  return value.map((item) => {
    const b = object(item, "bounding box");
    for (const key of ["x", "y", "width", "height"]) {
      if (
        typeof b[key] !== "number" ||
        !Number.isFinite(b[key]) ||
        b[key] < 0 ||
        b[key] > 1
      )
        throw new Error(
          "Некорректный ответ API: координаты рамки должны быть нормализованы от 0 до 1.",
        );
    }
    const x = b.x as number,
      y = b.y as number,
      width = b.width as number,
      height = b.height as number;
    if (!width || !height || x + width > 1.000001 || y + height > 1.000001)
      throw new Error(
        "Некорректный ответ API: рамка выходит за пределы кадра.",
      );
    const box: BoundingBox = {
      x,
      y,
      width,
      height,
      label: typeof b.label === "string" ? b.label : "Область внимания AI",
    };
    if (b.start_sec !== undefined)
      box.start_sec = timestamp(b.start_sec, undefined, "start_sec");
    if (b.end_sec !== undefined)
      box.end_sec = timestamp(b.end_sec, undefined, "end_sec");
    if (
      box.start_sec !== undefined &&
      box.end_sec !== undefined &&
      box.end_sec < box.start_sec
    )
      throw new Error("Некорректный ответ API: интервал рамки перевёрнут.");
    return box;
  });
}
function parseEvidence(value: unknown, duration?: number): Evidence {
  const frame = object(value, "evidence frame");
  const raw = requiredString(frame.image_base64, "image_base64");
  const base64 = raw.replace(/^data:image\/jpeg;base64,/, "");
  // JPEG SOI and a valid base64 alphabet are checked before ever displaying a response.
  if (
    !/^\/9j\//.test(base64) ||
    !/^[A-Za-z0-9+/]+={0,2}$/.test(base64) ||
    base64.length % 4 !== 0
  )
    throw new Error("Некорректный ответ API: ожидается JPEG в image_base64.");
  return {
    timestamp_sec: timestamp(frame.timestamp_sec, duration, "timestamp_sec"),
    image_base64: base64,
    boxes: parseBoxes(frame.boxes),
  };
}

export function parseResult(value: unknown, duration?: number): AnalysisResult {
  const body = object(value, "result");
  if (body.verdict !== "violation" && body.verdict !== "no_violation")
    throw new Error("Некорректный ответ API: неизвестный verdict.");
  if (!Array.isArray(body.violations))
    throw new Error("Некорректный ответ API: violations должен быть массивом.");
  if (
    (body.verdict === "no_violation" && body.violations.length > 0) ||
    (body.verdict === "violation" && body.violations.length === 0)
  )
    throw new Error(
      "Некорректный ответ API: verdict противоречит списку нарушений.",
    );
  const violations: Violation[] = body.violations.map((item, index) => {
    const v = object(item, "violation");
    if (
      !Array.isArray(v.evidence_frames) ||
      v.evidence_frames.length < 1 ||
      v.evidence_frames.length > 3
    )
      throw new Error(
        "Некорректный ответ API: ожидается от 1 до 3 кадров для каждого события.",
      );
    const violation: Violation = {
      id: typeof v.id === "string" && v.id ? v.id : `event-${index + 1}`,
      type: requiredString(v.type, "type"),
      category:
        typeof v.category === "string" && v.category.trim()
          ? v.category.trim()
          : "Требует проверки",
      timecode_sec: timestamp(v.timecode_sec, duration, "timecode_sec"),
      description: requiredString(v.description, "description"),
      evidence_frames: v.evidence_frames.map((frame) =>
        parseEvidence(frame, duration),
      ),
      review: "pending",
    };
    if (v.confidence !== undefined) {
      if (
        typeof v.confidence !== "number" ||
        !Number.isFinite(v.confidence) ||
        v.confidence < 0 ||
        v.confidence > 1
      )
        throw new Error(
          "Некорректный ответ API: confidence должен быть от 0 до 1.",
        );
      violation.confidence = v.confidence;
    }
    if (v.rule !== undefined) {
      const rule = object(v.rule, "rule");
      violation.rule = { title: requiredString(rule.title, "rule.title") };
      if (typeof rule.note === "string") violation.rule.note = rule.note;
      if (typeof rule.url === "string") {
        let url: URL;
        try {
          url = new URL(rule.url);
        } catch {
          throw new Error("Некорректный ответ API: rule.url.");
        }
        if (url.protocol !== "https:" && url.protocol !== "http:")
          throw new Error(
            "Некорректный ответ API: недопустимый протокол ссылки на правило.",
          );
        violation.rule.url = url.href;
      }
    }
    return violation;
  });
  if (new Set(violations.map((v) => v.id)).size !== violations.length)
    throw new Error(
      "Некорректный ответ API: повторяющиеся идентификаторы событий.",
    );
  return { verdict: body.verdict, violations };
}

export function parseSubmittedJob(value: unknown): string {
  return requiredString(object(value, "job").job_id, "job_id");
}

export function parseJobResponse(
  value: unknown,
  duration?: number,
): {
  status: "processing" | "done" | "failed";
  result?: AnalysisResult;
  error?: string;
} {
  const body = object(value, "job");
  if (body.status === "processing") return { status: "processing" };
  if (body.status === "failed")
    return {
      status: "failed",
      error:
        typeof body.error === "string" && body.error.trim()
          ? body.error
          : "Backend сообщил об ошибке обработки.",
    };
  if (body.status === "done")
    return { status: "done", result: parseResult(body, duration) };
  throw new Error("Некорректный ответ API: неизвестный status задачи.");
}
