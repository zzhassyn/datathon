import { expect, it } from "vitest";
import { parseLocation, validJobId } from "./urlState";

it("preserves job priority including invalid empty IDs", () => {
  expect(parseLocation("?job=&view=types")).toEqual({ view: "job", id: "" });
  expect(parseLocation("?view=types")).toEqual({ view: "types" });
  expect(validJobId("a".repeat(32))).toBe(true);
  expect(validJobId("../jobs")).toBe(false);
});
