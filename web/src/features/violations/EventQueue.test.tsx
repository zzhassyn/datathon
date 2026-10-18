import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { decodeJob } from "../../shared/api/decode";
import { EventQueue } from "./EventQueue";

it("keeps the real failed stage and its reason visible without a clean result", () => {
  const id = "a".repeat(32);
  const job = decodeJob(
    {
      job_id: id,
      status: "failed",
      pipeline_mode: "full",
      created_at: "2026-10-18T00:00:00Z",
      elapsed_sec: 4,
      verdict: null,
      violations: [],
      error: { code: "PIPELINE_CRASH", message_ru: "Калибровка не подтверждена" },
      stages: [
        {
          code: "rules",
          status: "failed",
          progress: 0,
          note_ru: "Не подтверждена поверхность кассира",
        },
      ],
    },
    id,
  );
  const html = renderToStaticMarkup(<EventQueue job={job} selected={0} onSelect={() => {}} />);
  expect(html).toContain("Не подтверждена поверхность кассира");
  expect(html).not.toContain("Нарушений не обнаружено");
  expect(html).not.toContain("Ожидаем события");
});
