import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { uploadVideo } from "./uploadVideo";

class FakeXHR {
  static last: FakeXHR;
  status = 200;
  responseText = JSON.stringify({ job_id: "a".repeat(32) });
  timeout = 0;
  upload = { onprogress: null as null | ((e: ProgressEvent) => void) };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  onabort: (() => void) | null = null;
  open = vi.fn();
  send = vi.fn();
  getResponseHeader = vi.fn(() => "2");
  abort = () => this.onabort?.();
  constructor() {
    FakeXHR.last = this;
  }
}
beforeEach(() => vi.stubGlobal("XMLHttpRequest", FakeXHR));
afterEach(() => vi.unstubAllGlobals());
const file = new File(["fixture"], "test.mov");
it("sends multipart, reports progress and accepts only valid identity", async () => {
  const report = vi.fn();
  const p = uploadVideo(file, report, new AbortController().signal);
  const x = FakeXHR.last;
  expect(x.open).toHaveBeenCalledWith("POST", "/api/ui/jobs");
  expect(x.send.mock.calls[0][0].get("file")).toBe(file);
  x.upload.onprogress?.({ lengthComputable: true, total: 100, loaded: 50 } as ProgressEvent);
  expect(report.mock.calls[0][0].percent).toBe(50);
  x.onload?.();
  await expect(p).resolves.toBe("a".repeat(32));
});
it.each([413, 415, 422, 429, 507, 502, 401])(
  "maps HTTP %s including retry-after",
  async (status) => {
    const p = uploadVideo(file, () => {}, new AbortController().signal);
    FakeXHR.last.status = status;
    FakeXHR.last.onload?.();
    await expect(p).rejects.toMatchObject({ status });
  },
);
it("aborts and removes callback after success", async () => {
  const c = new AbortController();
  const p = uploadVideo(file, () => {}, c.signal);
  c.abort();
  await expect(p).rejects.toMatchObject({ status: 499 });
  const c2 = new AbortController();
  const p2 = uploadVideo(file, () => {}, c2.signal);
  FakeXHR.last.onload?.();
  await p2;
  c2.abort();
});
it("rejects malformed successful response and network loss", async () => {
  const p = uploadVideo(file, () => {}, new AbortController().signal);
  FakeXHR.last.responseText = '{"job_id":"bad"}';
  FakeXHR.last.onload?.();
  await expect(p).rejects.toThrow();
  const n = uploadVideo(file, () => {}, new AbortController().signal);
  FakeXHR.last.onerror?.();
  await expect(n).rejects.toMatchObject({ status: 0 });
});
