import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { decodeJob } from "../shared/api/decode";
import { useJob } from "../shared/api/queries";
import { JobPage } from "./JobPage";

vi.mock("../shared/api/queries", () => ({
  active: (status: string) => ["queued", "running"].includes(status),
  useJob: vi.fn(),
}));

it.each(["full", "stub"] as const)(
  "offers operator calibration for failed real analysis only: %s",
  (mode) => {
    const id = "a".repeat(32);
    const job = decodeJob(
      {
        job_id: id,
        status: "failed",
        pipeline_mode: mode,
        created_at: "2026-10-18T00:00:00Z",
        elapsed_sec: 4,
        verdict: null,
        violations: [],
        error: { code: "PIPELINE_CRASH", message_ru: "Принадлежность не подтверждена" },
      },
      id,
    );
    vi.mocked(useJob).mockReturnValue({ data: job, isError: false } as ReturnType<typeof useJob>);
    const html = renderToStaticMarkup(<JobPage id={id} />);
    if (mode === "full") {
      expect(html).toContain('href="/local-calibration"');
      expect(html).toContain(
        "Нахождение вещи на столе или руки рядом не доказывает принадлежность",
      );
    } else {
      expect(html).not.toContain('href="/local-calibration"');
    }
    expect(html).not.toContain("Нарушение не обнаружено");
  },
);
