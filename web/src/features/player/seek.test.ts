import { expect, it } from "vitest";
import { clusterMarkers, markerPercent } from "./markers";
import { contextWindow } from "./seek";

it("bounds context to video and four seconds", () => {
  expect(contextWindow(7, 20)).toEqual({ start: 5, end: 9 });
  expect(contextWindow(0, 20)).toEqual({ start: 0, end: 2 });
  expect(contextWindow(19, 20)).toEqual({ start: 17, end: 20 });
});
it("clamps and clusters nearby events without losing their identity", () => {
  expect(markerPercent(100, 10)).toBe(100);
  expect(markerPercent(-1, 10)).toBe(0);
  const markers = [1, 1.1, 8].map((t, i) => ({
    id: String(i),
    time: t,
    title: String(i),
    index: i,
  }));
  const groups = clusterMarkers(markers, 10, 300);
  expect(groups).toHaveLength(2);
  expect(groups[0].items).toHaveLength(2);
});
