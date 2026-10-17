import { expect, it } from "vitest";
import { decodeJob, legalUrl, mediaUrl } from "./decode";

const id = "a".repeat(32);
const base = {
  job_id: id,
  status: "done",
  verdict: "no_violation",
  created_at: "2026-10-17T00:00:00Z",
  elapsed_sec: 1,
  violations: [],
  video: null,
};
it("never derives clean verdict from empty events", () => {
  expect(() => decodeJob({ ...base, verdict: null }, id)).toThrow();
  expect(() => decodeJob({ ...base, job_id: "b".repeat(32) }, id)).toThrow();
});
it("renders missing norms as absent without fabricating references", () => {
  const job = decodeJob(
    {
      ...base,
      verdict: "violation",
      violations: [
        { title_ru: "Synthetic", timecode_sec: 1, description_ru: "Test", evidence: [] },
      ],
    },
    id,
  );
  expect(job.violations[0].legal).toEqual([]);
  expect(job.violations[0].id).toBeNull();
});
it("rejects external media, base64 and non-Adilet normative links", () => {
  expect(() => mediaUrl("https://example.org/a.jpg")).toThrow();
  expect(() => mediaUrl("data:image/jpeg;base64,abc")).toThrow();
  expect(legalUrl("javascript:alert(1)")).toBeNull();
  expect(legalUrl("https://adilet.zan.kz.evil.test/path")).toBeNull();
  expect(legalUrl("https://adilet.zan.kz/rus/docs/V1900019680")).toContain("adilet.zan.kz");
});
