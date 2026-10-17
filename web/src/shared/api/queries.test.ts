import { expect, it } from "vitest";
import { HttpError } from "./errors";
import { pollInterval } from "./queries";

it("polls active jobs and recovers outages but stops terminal and auth/not-found", () => {
  for (const s of [undefined, "queued", "running"]) expect(pollInterval(s)).toBe(1000);
  for (const s of ["done", "failed"]) expect(pollInterval(s)).toBe(false);
  expect(pollInterval("running", new HttpError(503))).toBe(1000);
  expect(pollInterval(undefined, new HttpError(404))).toBe(false);
  expect(pollInterval("running", new HttpError(401))).toBe(false);
});
