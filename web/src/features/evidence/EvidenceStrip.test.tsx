import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { EvidenceStrip } from "./EvidenceStrip";

it("labels an end-of-event context frame without claiming it came before the event", () => {
  const html = renderToStaticMarkup(
    <EvidenceStrip
      title="Синтетический интервал 0–5 секунд"
      items={[
        {
          kind: "context",
          timestamp_sec: 5,
          url: `/api/ui/jobs/${"a".repeat(32)}/frames/01_context.jpg`,
          width: 160,
          height: 120,
        },
      ]}
      onSeek={() => {}}
    />,
  );
  expect(html).toContain("Кадр контекста, 00:05");
  expect(html).not.toContain("Кадр до события");
});
