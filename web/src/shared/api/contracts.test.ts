import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";
import { decodeJob } from "./decode";
import { HttpError } from "./errors";

const root = resolve(process.cwd(), "../tests/fixtures/ui");
for (const name of readdirSync(root).filter((n) => n.endsWith(".json"))) {
  it(`accepts agreed backend example ${name}`, () => {
    const fixture = JSON.parse(readFileSync(resolve(root, name), "utf8"));
    const view = decodeJob(fixture, fixture.job_id);
    expect(view.pipeline_mode).toBe(fixture.pipeline_mode);
    expect(view.violations).toHaveLength(fixture.violations.length);
    expect(view.verdict).toBe(fixture.verdict);
  });
}
it("shows contracted errors but never raw proxy bodies", () => {
  expect(new HttpError(422, 0, { code: "UPLOAD_TIMEOUT", message_ru: "Timeout" }).message).toBe(
    "Timeout",
  );
  expect(new HttpError(500, 0, "<h1>private stack</h1>").message).not.toContain("private");
  expect(
    new HttpError(429, 0, { code: "RATE_LIMITED", message_ru: "Wait", retry_after_sec: 3 }).retryAt,
  ).toBeGreaterThan(Date.now());
});
