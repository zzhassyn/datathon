import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { type PlayerHandle, VideoPlayer } from "./VideoPlayer";

const url = `/api/ui/jobs/${"a".repeat(32)}/video.mp4`;
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  HTMLElement.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
it("queues a seek until the preview and metadata exist, then bounds context playback", () => {
  const ref = createRef<PlayerHandle>();
  const props = {
    ref,
    localUrl: null,
    duration: 12,
    markers: [],
    finished: false,
    onSelect: vi.fn(),
  };
  const view = render(<VideoPlayer {...props} serverUrl={null} />);
  act(() => ref.current?.seek(3, true));
  view.rerender(<VideoPlayer {...props} serverUrl={url} />);
  const video = view.container.querySelector("video");
  expect(video).not.toBeNull();
  if (!video) throw new Error("Missing video");
  Object.defineProperty(video, "readyState", { value: 1, configurable: true });
  Object.defineProperty(video, "duration", { value: 12, configurable: true });
  fireEvent.loadedMetadata(video);
  expect(video.currentTime).toBe(1);
  expect(video.play).toHaveBeenCalled();
  video.currentTime = 5.05;
  fireEvent.timeUpdate(video);
  expect(video.pause).toHaveBeenCalledOnce();
});
it("changing to server preview preserves the paused position", () => {
  const ref = createRef<PlayerHandle>();
  const props = {
    ref,
    localUrl: "blob:synthetic",
    duration: 12,
    markers: [],
    finished: false,
    onSelect: vi.fn(),
  };
  const view = render(<VideoPlayer {...props} serverUrl={null} />);
  const local = view.container.querySelector("video");
  if (!local) throw new Error("Missing local video");
  local.currentTime = 6;
  view.rerender(<VideoPlayer {...props} serverUrl={url} />);
  const server = view.container.querySelector("video");
  if (!server) throw new Error("Missing server video");
  Object.defineProperty(server, "duration", { value: 12, configurable: true });
  fireEvent.loadedMetadata(server);
  expect(server.currentTime).toBe(6);
  expect(server.getAttribute("src")).toBe(url);
});
