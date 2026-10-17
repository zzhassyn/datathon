import type { Video } from "../types";
export function inspectVideo(file: File): Promise<Video> {
  const url = URL.createObjectURL(file);
  return new Promise((resolve) => {
    const element = document.createElement("video");
    element.preload = "metadata";
    element.muted = true;
    let done = false;
    const finish = (duration: number) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      element.onloadedmetadata = null;
      element.onerror = null;
      element.removeAttribute("src");
      element.load();
      resolve({
        id: crypto.randomUUID(),
        name: file.name,
        size: file.size,
        duration,
        url,
        available: true,
      });
    };
    const timer = window.setTimeout(() => finish(0), 5000);
    element.onloadedmetadata = () =>
      finish(Number.isFinite(element.duration) ? element.duration : 0);
    element.onerror = () => finish(0);
    element.src = url;
  });
}
export function captureFrame(
  url: string,
  seconds: number,
): Promise<string | undefined> {
  return new Promise((resolve) => {
    const video = document.createElement("video");
    video.muted = true;
    video.preload = "auto";
    video.crossOrigin = "anonymous";
    let done = false;
    const finish = (value?: string) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      video.onloadeddata = null;
      video.onseeked = null;
      video.onerror = null;
      video.removeAttribute("src");
      video.load();
      resolve(value);
    };
    const timer = window.setTimeout(() => finish(), 6000);
    video.onloadeddata = () => {
      if (seconds < 0.05) snap();
      else
        video.currentTime = Math.min(
          seconds,
          Math.max(0, video.duration - 0.1),
        );
    };
    const snap = () => {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        canvas.getContext("2d")?.drawImage(video, 0, 0);
        finish(canvas.toDataURL("image/jpeg", 0.8));
      } catch {
        finish();
      }
    };
    video.onseeked = snap;
    video.onerror = () => finish();
    video.src = url;
  });
}
