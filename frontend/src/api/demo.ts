import type {
  AnalysisJob,
  AnalysisResult,
  BoundingBox,
  Violation,
} from "../types";

export const DEMO_VIDEO_URL = "/demo/cash-desk.webm";
export const DEMO_FRAME_URL = "/demo/cash-desk.svg";

const demoBox: BoundingBox = {
  x: 0.465,
  y: 0.569,
  width: 0.076,
  height: 0.076,
  label: "Учебная область",
  start_sec: 6,
  end_sec: 10,
};

/** These are storyboard examples, never model detections or official rule interpretations. */
export function createDemoResult(
  scenario: "events" | "clean" | "error" = "events",
): AnalysisResult {
  if (scenario === "error")
    throw new Error(
      "Демонстрационная ошибка: анализ не завершён. Результат отсутствует.",
    );
  if (scenario === "clean") return { verdict: "no_violation", violations: [] };
  const violations: Violation[] = [
    {
      id: "demo-event-1",
      type: "Возможное отсутствие пересчёта",
      category: "Работа с наличностью",
      timecode_sec: 7.2,
      description:
        "Учебный пример: передача условной пачки наличности показана без видимого этапа пересчёта. В реальном анализе нужно проверить весь эпизод, ракурс и требования применимого правила. Это сценарная иллюстрация, а не вывод AI.",
      evidence_frames: [
        {
          timestamp_sec: 7.2,
          image_url: DEMO_FRAME_URL,
          boxes: [{ ...demoBox }],
          synthetic: true,
        },
      ],
      rule: {
        title: "Требует сверки с регламентом",
        note: "Сценарный кандидат. Соответствие конкретному пункту правил не проверялось.",
      },
      review: "pending",
    },
    {
      id: "demo-event-2",
      type: "Предмет вне рабочей зоны",
      category: "Порядок обслуживания",
      timecode_sec: 15.8,
      description:
        "Учебный пример: условный предмет оказывается за обозначенной рабочей зоной кассы. По одной рамке нельзя подтвердить нарушение: человеку нужно проверить контекст и действующий регламент. Рамка и описание заданы вручную для демонстрации интерфейса.",
      evidence_frames: [
        {
          timestamp_sec: 15.8,
          image_url: DEMO_FRAME_URL,
          boxes: [
            {
              x: 0.675,
              y: 0.522,
              width: 0.064,
              height: 0.112,
              label: "Учебная область",
              start_sec: 14,
              end_sec: 18,
            },
          ],
          synthetic: true,
        },
      ],
      rule: {
        title: "Требует сверки с регламентом",
        note: "Классификация показана только как пример будущего результата API.",
      },
      review: "pending",
    },
    {
      id: "demo-event-3",
      type: "Эпизод требует проверки",
      category: "Проверка процедуры",
      timecode_sec: 20.4,
      description:
        "Учебный пример третьего события: момент обслуживания помечен для ручной проверки. Система в этом режиме не обрабатывает кадры и не оценивает действия сотрудника.",
      evidence_frames: [
        { timestamp_sec: 20.4, image_url: DEMO_FRAME_URL, synthetic: true },
      ],
      review: "pending",
    },
  ];
  return { verdict: "violation", violations };
}

export function demoJobs(): AnalysisJob[] {
  const now = Date.now();
  const make = (
    id: string,
    name: string,
    minutesAgo: number,
    scenario: "events" | "clean" | "error",
  ): AnalysisJob => {
    const createdAt = new Date(now - minutesAgo * 60_000).toISOString();
    return {
      id,
      mode: "mock",
      video: {
        id: `${id}-video`,
        name,
        size: 0,
        duration: 24,
        url: DEMO_VIDEO_URL,
        previewUrl: DEMO_FRAME_URL,
        available: true,
        synthetic: true,
      },
      status: scenario === "error" ? "failed" : "done",
      createdAt,
      updatedAt: createdAt,
      progress: scenario === "error" ? undefined : 100,
      demoScenario: scenario,
      ...(scenario === "error"
        ? { error: "Учебный пример сбоя обработки. Результат не получен." }
        : { result: createDemoResult(scenario) }),
    };
  };
  const second = make("demo-002", "DEMO · касса 02.webm", 88, "events");
  second.result!.violations = second.result!.violations.slice(0, 2);
  return [
    make("demo-001", "DEMO · касса 01.webm", 24, "events"),
    second,
    make("demo-003", "DEMO · без кандидатов.webm", 170, "clean"),
    make("demo-004", "DEMO · ошибка обработки.webm", 240, "error"),
    {
      ...make("demo-005", "DEMO · текущая обработка.webm", 0, "events"),
      status: "processing",
      createdAt: new Date(now - 4_000).toISOString(),
      progress: 42,
      result: undefined,
    },
  ];
}
