import { describe, expect, it } from "vitest";
import { timecode } from "../src/lib/format";
describe("precise timecodes", () => {
  it.each([
    [0, "00:00"],
    [7.2, "00:07.2"],
    [20.4, "00:20.4"],
    [60.1, "01:00.1"],
    [59.99, "01:00"],
    [NaN, "00:00"],
    [-5, "00:00"],
  ])("formats %s as %s", (value, label) =>
    expect(timecode(value as number)).toBe(label),
  );
});
