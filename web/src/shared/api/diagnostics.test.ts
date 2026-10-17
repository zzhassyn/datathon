import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";
import { decodeJob } from "./decode";

function fixture() {
  return JSON.parse(
    readFileSync(resolve(process.cwd(), "../tests/fixtures/ui/08_diagnostics_demo.json"), "utf8"),
  );
}
it("preserves unknown conditions and uses actual evidence references", () => {
  const raw = fixture();
  const view = decodeJob(raw, raw.job_id);
  expect(view.violations[0].explanation?.conditions.map((item) => item.status)).toEqual([
    "met",
    "not_met",
    "unknown",
  ]);
  expect(view.violations[0].explanation?.conditions[2].evidence_indices).toEqual([]);
  expect(view.visibility?.checks[2].status).toBe("unknown");
  expect(view.visibility?.checks[2].start_sec).toBeNull();
  expect(view.verdict).toBe(raw.verdict);
});
it("reads old responses without inventing diagnostics", () => {
  const raw = fixture();
  delete raw.visibility;
  delete raw.violations[0].explanation;
  const view = decodeJob(raw, raw.job_id);
  expect(view.visibility).toBeNull();
  expect(view.violations[0].explanation).toBeNull();
});
it.each([[99], [-1], [0.5], [true], [0, 0], []].map((indices) => ({ indices })))(
  "rejects invalid evidence reference %j",
  ({ indices }) => {
    const raw = fixture();
    raw.violations[0].explanation.conditions[0].evidence_indices = indices;
    expect(() => decodeJob(raw, raw.job_id)).toThrow();
  },
);
it.each([
  [2, 1],
  [-1, 1],
  [0, Infinity],
  [NaN, 1],
  [true, 1],
  [null, 1],
  [null, null],
])("rejects invalid assessed interval %j", (start, end) => {
  const raw = fixture();
  raw.visibility.checks[0].start_sec = start;
  raw.visibility.checks[0].end_sec = end;
  expect(() => decodeJob(raw, raw.job_id)).toThrow();
});
it("rejects duplicated diagnostic identities", () => {
  const raw = fixture();
  raw.visibility.checks.push(raw.visibility.checks[0]);
  expect(() => decodeJob(raw, raw.job_id)).toThrow();
});
it("preserves visibility for a clean verdict and absent preview", () => {
  const raw = fixture();
  raw.verdict = "no_violation";
  raw.violations = [];
  raw.video = null;
  const view = decodeJob(raw, raw.job_id);
  expect(view.verdict).toBe("no_violation");
  expect(view.visibility?.checks).toHaveLength(3);
});
